"""Small, inspectable policy retrieval for the Merge Gate demo.

The MVP uses lexical retrieval plus domain-aware boosts. This is intentionally
simple enough to audit and evaluate. It can later be replaced by hybrid
BM25/vector retrieval without changing the rest of the pipeline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from model import Decision
from policies import infer_sensitive_domains, is_low_risk_only


POLICY_HEADING = re.compile(r"^##\s+([A-Z]+-\d+):\s+(.+?)\s*$")
TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    # Generic PR/control words create misleading policy matches. Control state
    # is handled deterministically and domain boosts handle policy routing.
    "add",
    "change",
    "changes",
    "ci",
    "failed",
    "fix",
    "irreversible",
    "only",
    "passed",
    "remove",
    "reversible",
    "update",
    "until",
}

DOMAIN_POLICY = {
    "authentication": "SEC-04",
    "payments": "PAY-01",
    "database": "DB-02",
    "infrastructure": "INFRA-03",
    "permissions": "SEC-04",
}


@dataclass(frozen=True)
class PolicyDocument:
    """One policy section parsed from the markdown knowledge file."""

    policy_id: str
    title: str
    text: str


@dataclass(frozen=True)
class PolicyMatch:
    """A scored policy hit for a PR, including which query terms overlapped."""

    policy_id: str
    title: str
    text: str
    score: float
    matched_terms: tuple[str, ...]


def tokenize(text: str) -> set[str]:
    """Lowercase-split text into content tokens, dropping stopwords."""
    return {token for token in TOKEN.findall(text.lower()) if token not in STOPWORDS}


@lru_cache(maxsize=8)
def load_policy_documents(path: str = "knowledge/repository_policies.md") -> tuple[PolicyDocument, ...]:
    """Parse ## POLICY-ID: Title sections from markdown; cached by path."""
    source = Path(path)
    lines = source.read_text(encoding="utf-8").splitlines()
    documents: list[PolicyDocument] = []
    current_id: str | None = None
    current_title = ""
    body: list[str] = []

    def flush() -> None:
        """Commit the in-progress section into documents, then reset the body."""
        nonlocal body, current_id, current_title
        if current_id is not None:
            documents.append(
                PolicyDocument(
                    policy_id=current_id,
                    title=current_title,
                    text="\n".join(body).strip(),
                )
            )
        body = []

    for line in lines:
        match = POLICY_HEADING.match(line)
        if match:
            flush()
            current_id, current_title = match.groups()
        elif current_id is not None:
            body.append(line)
    flush()

    if not documents:
        raise ValueError(f"No policy sections were found in {source}")
    return tuple(documents)


def retrieve_policies(
    decision: Decision,
    *,
    path: str = "knowledge/repository_policies.md",
    k: int = 3,
) -> list[PolicyMatch]:
    """Rank policies for a PR via token overlap + domain/incident boosts; return top-k with score > 0."""

    if k < 1:
        raise ValueError("k must be at least 1")

    query_text = " ".join(
        [
            decision.title,
            *decision.files_touched,
            decision.diff_excerpt,
            "incident" if decision.touches_incident_code is True else "",
            "irreversible" if decision.reversible is False else "",
            "ci failed" if decision.ci_passed is False else "",
        ]
    )
    query_tokens = tokenize(query_text)
    # A sensitive word in a documentation/test path is context, not proof that
    # the sensitive runtime boundary changed. LOW-01 gets the applicability
    # boost; other matching policies may still appear with lexical relevance.
    if is_low_risk_only(decision):
        boosted_ids = {"LOW-01"}
    else:
        boosted_ids = {
            DOMAIN_POLICY[domain]
            for domain in infer_sensitive_domains(decision)
            if domain in DOMAIN_POLICY
        }
    if decision.touches_incident_code is True:
        boosted_ids.add("INC-05")

    matches: list[PolicyMatch] = []
    for document in load_policy_documents(path):
        document_tokens = tokenize(f"{document.title} {document.text}")
        matched_terms = tuple(sorted(query_tokens & document_tokens))
        overlap = len(matched_terms) / max(1, len(document_tokens) ** 0.5)
        boost = 4.0 if document.policy_id in boosted_ids else 0.0
        score = overlap + boost
        matches.append(
            PolicyMatch(
                policy_id=document.policy_id,
                title=document.title,
                text=document.text,
                score=round(score, 4),
                matched_terms=matched_terms,
            )
        )

    matches.sort(key=lambda item: (-item.score, item.policy_id))
    # An empty retrieval result is more honest than padding the context with
    # zero-relevance policies. Unknown changes already fall back to review.
    return [match for match in matches if match.score > 0.0][:k]
