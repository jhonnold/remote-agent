# src/remote_agent/agent.py
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.exceptions import AgentError
from remote_agent.prompts.planning import build_planning_system_prompt, build_planning_user_prompt
from remote_agent.prompts.implementation import build_implementation_system_prompt, build_implementation_user_prompt

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    success: bool
    session_id: str | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    result_text: str | None = None
    error: str | None = None


@dataclass
class CommentInterpretation:
    intent: str  # "approve", "revise", "question", "back_to_planning"
    response: str | None = None


class AgentService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    async def run_planning(self, *, issue_number: int, issue_title: str,
                            issue_body: str, cwd: str, issue_id: int,
                            existing_plan: str | None = None,
                            feedback: str | None = None) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_planning_system_prompt()
        user_prompt = build_planning_user_prompt(
            issue_number=issue_number, issue_title=issue_title,
            issue_body=issue_body, existing_plan=existing_plan, feedback=feedback,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebSearch", "Agent"],
            permission_mode="bypassPermissions",
            model=self.config.agent.planning_model,
            max_turns=self.config.agent.max_turns,
            max_budget_usd=self.config.agent.max_budget_usd,
            cwd=cwd,
            agents=self._get_planning_subagents(),
        )
        return await self._run_query(user_prompt, options, issue_id, phase="planning", allow_resume=True)

    async def run_implementation(self, *, plan_content: str, issue_title: str,
                                  cwd: str, issue_id: int,
                                  feedback: str | None = None) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_implementation_system_prompt()
        user_prompt = build_implementation_user_prompt(
            plan_content=plan_content, issue_title=issue_title, feedback=feedback,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep", "Bash", "Agent"],
            permission_mode="bypassPermissions",
            model=self.config.agent.orchestrator_model,
            max_turns=self.config.agent.max_turns,
            max_budget_usd=self.config.agent.max_budget_usd,
            cwd=cwd,
            agents=self._get_implementation_subagents(),
        )
        return await self._run_query(user_prompt, options, issue_id, phase="implementing", allow_resume=True)

    async def interpret_comment(self, *, comment: str, context: str,
                                 issue_title: str, issue_id: int) -> CommentInterpretation:
        return self._classify_comment_text(comment, context)

    async def _run_query(self, prompt: str, options, issue_id: int, phase: str,
                          allow_resume: bool = False) -> AgentResult:
        from claude_agent_sdk import query, ResultMessage

        logger.info("Starting %s query for issue %d, model=%s", phase, issue_id, getattr(options, "model", "unknown"))

        run_id = await self.db.create_agent_run(issue_id, phase)

        # Support session resumption on retry
        if allow_resume:
            prev_session = await self.db.get_latest_session_for_phase(issue_id, phase)
            if prev_session:
                options.resume = prev_session
                logger.info("Resuming session %s for issue %d phase %s", prev_session, issue_id, phase)

        session_id = None
        result_text = None
        cost = 0.0
        input_tokens = 0
        output_tokens = 0

        try:
            async for message in query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    session_id = message.session_id
                    result_text = message.result
                    cost = message.total_cost_usd or 0.0
                    usage = message.usage or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)

            logger.info("Completed %s query for issue %d, cost=$%.4f, tokens=%d+%d, session=%s",
                        phase, issue_id, cost, input_tokens, output_tokens, session_id)

            await self.db.complete_agent_run(
                run_id, session_id=session_id, result="success",
                cost_usd=cost, input_tokens=input_tokens, output_tokens=output_tokens,
            )
            return AgentResult(
                success=True, session_id=session_id, cost_usd=cost,
                input_tokens=input_tokens, output_tokens=output_tokens,
                result_text=result_text,
            )
        except Exception as e:
            logger.warning("Query failed for issue %d phase=%s: %s", issue_id, phase, e)
            await self.db.complete_agent_run(
                run_id, result="error", cost_usd=cost,
                input_tokens=input_tokens, output_tokens=output_tokens,
                error_message=str(e),
            )
            raise AgentError(str(e)) from e

    def _get_planning_subagents(self) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "codebase-explorer": AgentDefinition(
                description="Explores the codebase to understand structure, patterns, and conventions. Use this to research the repo before creating the plan.",
                prompt="You are a codebase exploration specialist. Analyze the code structure, find patterns, understand conventions, and report findings clearly and concisely. Focus on: project structure, testing patterns, key abstractions, and coding style.",
                tools=["Read", "Glob", "Grep"],
                model="haiku",
            ),
        }

    def _get_implementation_subagents(self) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "implementer": AgentDefinition(
                description="Implements a specific task from the plan. Use for each individual implementation task.",
                prompt="""You are a skilled developer implementing a specific task. You will receive the full task description including files to create/modify, tests to write, and implementation details.

## Process
1. Read the task carefully
2. Write the failing test first
3. Run it to verify it fails
4. Write the minimal implementation to pass
5. Run tests to verify they pass
6. Self-review your work

## Rules
- Follow the task instructions exactly
- Use test-driven development
- Follow existing codebase patterns
- Do not modify files outside the task scope
- Run tests after every change

## Report
When done, report:
- Status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- What you implemented
- Tests written and their results
- Files changed
- Any concerns or issues found during self-review
""",
                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                model="sonnet",
            ),
            "spec-reviewer": AgentDefinition(
                description="Reviews implementation for spec compliance. Use after each task is implemented.",
                prompt="""You are a spec compliance reviewer. Your job is to verify that the implementation exactly matches what was requested.

## What to Check
- Every requirement in the task is implemented
- Nothing extra was added (YAGNI)
- Nothing was missed or misunderstood
- Tests exist and test the right things
- Code matches the file paths specified in the task

## CRITICAL
Do NOT trust the implementer's report. Read the actual code and tests yourself.

## Output
- APPROVED: Implementation matches spec exactly
- ISSUES FOUND: List specific issues with file:line references
""",
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "code-reviewer": AgentDefinition(
                description="Reviews code quality after spec compliance passes. Use after spec-reviewer approves.",
                prompt="""You are a code quality reviewer. The implementation has already passed spec compliance review. Now verify it is well-built.

## What to Check
- Code is clean and readable
- Tests are meaningful (not just coverage padding)
- Follows existing codebase patterns and conventions
- No security issues
- Error handling is appropriate
- File decomposition is correct (one responsibility per file)

## Output
- APPROVED: Code quality is good
- ISSUES FOUND: List specific issues with file:line references, categorized as Critical/Important/Minor
""",
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
        }

    # Patterns for review header, approval phrases, and back-to-planning phrases
    _REVIEW_HEADER_RE = re.compile(r'^\[Review\s*[—–-]\s*(\w+)\]\s*', re.MULTILINE)
    _APPROVE_RE = re.compile(
        r'\b(lgtm|looks?\s+good|approved?|ship\s+it|go\s+ahead)\b', re.IGNORECASE,
    )
    _BACK_TO_PLANNING_RE = re.compile(
        r'\b(back\s+to\s+planning|rethink|plan\s+needs?\s+to\s+change)\b', re.IGNORECASE,
    )

    def _classify_comment_text(self, comment: str, context: str) -> CommentInterpretation:
        # Extract review state from formatted header (e.g. "[Review — APPROVED]")
        header_match = self._REVIEW_HEADER_RE.match(comment)
        review_state = header_match.group(1).upper() if header_match else None

        # Strip header to get the actual body text
        body = self._REVIEW_HEADER_RE.sub('', comment).strip()

        # Separate inline comments section from body text
        inline_idx = body.find('\nInline comments:\n')
        has_inline = inline_idx >= 0
        text_body = body[:inline_idx].strip() if has_inline else body

        # GitHub review state is the strongest signal
        if review_state == 'APPROVED':
            return CommentInterpretation(intent="approve")
        if review_state == 'CHANGES_REQUESTED':
            return CommentInterpretation(intent="revise")

        # Check for approval phrases (only when no inline revision comments)
        if self._APPROVE_RE.search(text_body) and not has_inline:
            return CommentInterpretation(intent="approve")

        # Check for back-to-planning (code_review only)
        if context == 'code_review' and self._BACK_TO_PLANNING_RE.search(text_body):
            return CommentInterpretation(intent="back_to_planning")

        # Simple question detection
        if '?' in text_body:
            return CommentInterpretation(intent="question")

        # Default to revise (safe fallback)
        return CommentInterpretation(intent="revise")
