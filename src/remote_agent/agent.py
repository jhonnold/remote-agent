# src/remote_agent/agent.py
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass

from remote_agent.config import Config
from remote_agent.db import Database
from remote_agent.exceptions import AgentError
from remote_agent.prompts.designing import build_designing_system_prompt, build_designing_user_prompt
from remote_agent.prompts.planning import build_planning_system_prompt, build_planning_user_prompt
from remote_agent.prompts.implementation import build_implementation_system_prompt, build_implementation_user_prompt
from remote_agent.prompts.review import build_review_system_prompt, build_review_user_prompt
from remote_agent.prompts.subagents import (
    codebase_explorer_prompt, issue_advocate_prompt, design_critic_prompt,
    plan_reviewer_prompt, implementer_prompt, spec_reviewer_prompt,
    code_quality_reviewer_prompt, final_reviewer_prompt,
)

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
    intent: str  # "approve", "revise", "question", "back_to_design"
    response: str | None = None


class AgentService:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    async def run_designing(self, *, issue_number: int, issue_title: str,
                             issue_body: str, cwd: str, issue_id: int,
                             existing_design: str | None = None,
                             feedback: str | None = None) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_designing_system_prompt()
        user_prompt = build_designing_user_prompt(
            issue_number=issue_number, issue_title=issue_title,
            issue_body=issue_body, existing_design=existing_design, feedback=feedback,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebSearch", "Agent"],
            permission_mode="bypassPermissions",
            model=self.config.agent.planning_model,
            max_turns=self.config.agent.max_turns,
            max_budget_usd=self.config.agent.max_budget_usd,
            cwd=cwd,
            agents=self._get_designing_subagents(issue_body),
        )
        return await self._run_query(user_prompt, options, issue_id, phase="designing", allow_resume=True)

    async def run_planning(self, *, issue_number: int, issue_title: str,
                            issue_body: str, design_content: str,
                            cwd: str, issue_id: int) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_planning_system_prompt()
        user_prompt = build_planning_user_prompt(
            issue_number=issue_number, issue_title=issue_title,
            issue_body=issue_body, design_content=design_content,
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
                                  issue_body: str = "", design_content: str = "",
                                  cwd: str, issue_id: int,
                                  feedback: str | None = None) -> AgentResult:
        from claude_agent_sdk import query, ClaudeAgentOptions

        system_prompt = build_implementation_system_prompt()
        user_prompt = build_implementation_user_prompt(
            plan_content=plan_content, issue_title=issue_title,
            issue_body=issue_body, design_content=design_content,
            feedback=feedback,
        )

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep", "Bash", "Agent"],
            permission_mode="bypassPermissions",
            model=self.config.agent.orchestrator_model,
            max_turns=self.config.agent.max_turns,
            max_budget_usd=self.config.agent.max_budget_usd,
            cwd=cwd,
            agents=self._get_implementation_subagents(issue_body),
        )
        return await self._run_query(user_prompt, options, issue_id, phase="implementing", allow_resume=True)

    async def interpret_comment(self, *, comment: str, context: str,
                                 issue_title: str, issue_id: int,
                                 design_content: str | None = None,
                                 plan_content: str | None = None) -> CommentInterpretation:
        return self._classify_comment_text(comment, context)

    async def answer_question(self, *, question: str, context: str,
                               issue_title: str, issue_body: str,
                               design_content: str | None = None,
                               plan_content: str | None = None) -> str:
        from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

        system_prompt = (
            f"You are answering a question about {context}. "
            "Use the provided context to give a clear, helpful answer."
        )

        parts = [f"**Question:** {question}\n"]
        parts.append(f"**Issue:** {issue_title}\n")
        if issue_body:
            parts.append(f"## Issue Body\n\n{issue_body}\n")
        if design_content:
            parts.append(f"## Design Document\n\n{design_content}\n")
        if plan_content:
            parts.append(f"## Implementation Plan\n\n{plan_content}\n")
        user_prompt = "\n".join(parts)

        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="bypassPermissions",
            model=self.config.agent.review_model,
            max_turns=20,
            max_budget_usd=1.0,
        )

        result_text = ""
        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, ResultMessage):
                result_text = message.result or ""

        return result_text

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

    def _get_designing_subagents(self, issue_body: str) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "codebase-explorer": AgentDefinition(
                description="Explores the codebase to understand structure, patterns, and conventions. Use this to research the repo before proposing designs.",
                prompt=codebase_explorer_prompt(),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "issue-advocate": AgentDefinition(
                description="Represents the issue author's intent. Use to ask clarifying questions about the issue and validate assumptions.",
                prompt=issue_advocate_prompt(issue_body),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "design-critic": AgentDefinition(
                description="Stress-tests design sections for completeness, feasibility, and alignment. Present sections one at a time for critique.",
                prompt=design_critic_prompt(),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
        }

    def _get_planning_subagents(self) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "codebase-explorer": AgentDefinition(
                description="Explores the codebase to understand structure, patterns, and conventions. Use this to research the repo before creating the plan.",
                prompt=codebase_explorer_prompt(),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "plan-reviewer": AgentDefinition(
                description="Validates the implementation plan against the design doc. Use after drafting the plan to check for gaps.",
                prompt=plan_reviewer_prompt(),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
        }

    def _get_implementation_subagents(self, issue_body: str) -> dict:
        from claude_agent_sdk import AgentDefinition
        return {
            "implementer": AgentDefinition(
                description="Implements a specific task from the plan. Use for each individual implementation task.",
                prompt=implementer_prompt(),
                tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
                model="sonnet",
            ),
            "spec-reviewer": AgentDefinition(
                description="Reviews implementation for spec compliance. Use after each task is implemented.",
                prompt=spec_reviewer_prompt(),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "code-reviewer": AgentDefinition(
                description="Reviews code quality after spec compliance passes. Use after spec-reviewer approves.",
                prompt=code_quality_reviewer_prompt(),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "issue-advocate": AgentDefinition(
                description="Represents the issue author's intent. Use to answer implementer questions about requirements.",
                prompt=issue_advocate_prompt(issue_body),
                tools=["Read", "Glob", "Grep"],
                model="sonnet",
            ),
            "final-reviewer": AgentDefinition(
                description="Performs a holistic review of the entire changeset after all tasks are complete.",
                prompt=final_reviewer_prompt(),
                tools=["Read", "Glob", "Grep", "Bash"],
                model="sonnet",
            ),
        }

    # Patterns for review header, approval phrases, and back-to-planning phrases
    _REVIEW_HEADER_RE = re.compile(r'^\[Review\s*[—–-]\s*(\w+)\]\s*', re.MULTILINE)
    _APPROVE_RE = re.compile(
        r'\b(lgtm|looks?\s+good|approved?|ship\s+it|go\s+ahead)\b', re.IGNORECASE,
    )
    _BACK_TO_DESIGN_RE = re.compile(
        r'\b(back\s+to\s+design|rethink\s+the\s+design|design\s+needs?\s+to\s+change)\b', re.IGNORECASE,
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

        # Check for back-to-design (code_review only)
        if context == 'code_review' and self._BACK_TO_DESIGN_RE.search(text_body):
            return CommentInterpretation(intent="back_to_design")

        # Simple question detection
        if '?' in text_body:
            return CommentInterpretation(intent="question")

        # Default to revise (safe fallback)
        return CommentInterpretation(intent="revise")
