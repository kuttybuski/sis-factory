"""Pattern rules. Every quantifier is bounded, so scanning time stays linear in the input size."""

import re
from dataclasses import dataclass

from injection_guard.models import Severity

_F = re.IGNORECASE | re.DOTALL


@dataclass(frozen=True)
class Rule:
    id: str
    category: str
    severity: Severity
    weight: int
    pattern: re.Pattern[str]


def _r(id: str, category: str, severity: Severity, weight: int, pattern: str) -> Rule:
    return Rule(id, category, severity, weight, re.compile(pattern, _F))


H, M, L, C = Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.CRITICAL

RULES: tuple[Rule, ...] = (
    # --- Overriding the agent's instructions
    _r("override-instructions", "override", H, 45,
       r"\b(ignore|disregard|forget|override|bypass|discard)\b.{0,40}\b(previous|prior|above|earlier|preceding|all|any|your|the|these)\b.{0,30}"
       r"\b(instructions?|prompts?|rules?|guidelines?|directions?|directives?|constraints?|guardrails?)\b"),
    _r("new-instructions", "override", M, 25,
       r"\b(new|updated|real|actual|additional|revised|hidden)\s+(instructions?|directives?|system prompt|task)\s*[:\-—]"),
    _r("stop-following", "override", H, 40,
       r"\b(stop|cease|do not|don't|no longer)\b.{0,12}\b(follow(ing)?|obey(ing)?|adher(e|ing) to)\b.{0,30}\b(instructions?|rules?|guidelines?|system prompt)\b"),
    # --- Impersonating a privileged role or chat format
    _r("role-impersonation", "impersonation", H, 40,
       r"(^|\n)\s*(#{1,4}\s*(system|developer|assistant)\b|(system|developer)\s+(message|prompt)\s*:|assistant\s*:)|"
       r"<\|?(im_start|im_end|system|assistant|endoftext)\|?>|\[/?INST\]|<<\s*/?SYS\s*>>|<\s*/?(system|assistant|developer)\s*>"),
    _r("authority-claim", "impersonation", M, 20,
       r"\b(admin(istrator)?|security team|ceo|cto|owner|maintainer|anthropic|openai)\b.{0,25}"
       r"\b(override|authori[sz]ed|approved|instructs?|requires?|demands?|orders?)\b"),
    # --- Leaking the prompt or secrets
    _r("prompt-exfil", "exfiltration", H, 45,
       r"\b(reveal|show|print|repeat|output|leak|display|tell me|write out|dump)\b.{0,40}"
       r"\b(system prompt|initial prompt|your instructions|hidden instructions|your rules|your prompt|hidden prompt)\b"),
    _r("exfil-verb", "exfiltration", H, 45, r"\bexfiltrat\w*"),
    _r("secret-to-external", "exfiltration", C, 60,
       r"\b(send|post|upload|transmit|forward|leak|share|email|push|curl)\b.{0,60}"
       r"(?:\b(?:secrets?|tokens?|credentials?|api[ _-]?keys?|passwords?|ssh keys?|private keys?)\b|(?<!\w)\.env\b).{0,80}"
       r"(\bhttps?://|\bemail\b|\bpastebin\b|\bwebhook\b|\bmy (own )?server\b|@[\w.-]+\.[a-z]{2,})|"
       r"\b(send|post|upload|transmit|forward|curl|push)\b.{0,80}\bhttps?://.{0,80}"
       r"(?:\b(?:secrets?|tokens?|credentials?|api[ _-]?keys?|passwords?|ssh keys?|private keys?)\b|(?<!\w)\.env\b)"),
    _r("secret-read", "exfiltration", H, 45,
       r"\b(print|cat|read|dump|echo|type|open)\b.{0,30}(\.env\b|id_rsa|\.ssh/|/etc/passwd|\.aws/credentials|"
       r"\$\{?[A-Z_]*(TOKEN|SECRET|PASSWORD|API_?KEY)[A-Z_]*\}?)"),
    _r("url-beacon", "exfiltration", M, 35,
       r"!\[[^\]]{0,100}\]\(\s*https?://[^)\s]{1,200}\?[^)\s]{16,300}\)"),
    _r("append-to-url", "exfiltration", M, 30,
       r"\b(append|add|include|encode)\b.{0,50}\b(to|in|into)\b.{0,15}\b(the )?(url|link|query string|request)\b"),
    # --- Hiding the attack from people
    _r("concealment", "stealth", H, 40,
       r"\b(do not|don't|never)\s+(tell|inform|notify|mention|reveal|alert|warn)\s+(the\s+)?"
       r"(user|human|reviewers?|anyone|team|owner|maintainers?)\s+(about|that|of)\b|"
       r"\bwithout\s+(telling|informing|notifying|alerting)\s+(the\s+)?(user|human|reviewers?|anyone|team|owner|maintainers?)\b"),
    _r("secretly", "stealth", M, 25,
       r"\b(secretly|covertly|silently|quietly)\b.{0,30}\b(send|upload|post|add|change|modify|push|commit|install|disable)\b"),
    _r("hidden-html-comment", "stealth", H, 40,
       r"<!--.{0,400}?\b(ignore|instruction|system prompt|assistant|you must|exfiltrate)\b.{0,400}?-->"),
    _r("hidden-styling", "stealth", M, 20,
       r"(display\s*:\s*none|font-size\s*:\s*0|visibility\s*:\s*hidden|color\s*:\s*(white|#fff(fff)?)\b)"),
    # --- Persona and jailbreak
    _r("jailbreak-persona", "jailbreak", M, 30,
       r"\b(do anything now|developer mode|jailbreak(ed)?|no restrictions|without (any )?(restrictions|filters|safety|limitations)|"
       r"act as (an? )?(unrestricted|unfiltered|jailbroken|root|admin))\b|\bDAN\b"),
    _r("pretend", "jailbreak", L, 15, r"\b(pretend|imagine|roleplay)\b.{0,20}\b(you are|you're|to be|that you)\b"),
    # --- Talking to the model rather than to the team
    _r("addresses-ai", "targeting", M, 25,
       r"\b(if|when)\s+you\s+are\s+(an?\s+)?(ai|llm|language model|assistant|agent|bot|large language model)\b"),
    _r("directive-to-ai", "targeting", L, 12,
       r"\b(ai|llm|language model|assistant|chatgpt|claude|copilot|coding agent)\b.{0,30}\b(must|should|need to|are required to|will now|have to)\b"),
    # --- Breaking out of our own delimiters
    _r("delimiter-escape", "delimiter", H, 35,
       r"</?\s*(untrusted|context|document|data|instructions?|system|user|tool_?(result|output|response)|ticket|retrieved)[^>]{0,80}>|"
       r"-----\s*(BEGIN|END)\s+UNTRUSTED"),
    # --- Dangerous actions
    _r("pipe-to-shell", "dangerous-command", M, 30,
       r"\b(curl|wget)\b[^|\n]{0,200}\|\s*(sudo\s+)?(ba|z)?sh\b|base64\s+(-d|--decode)[^|\n]{0,100}\|\s*(ba)?sh\b"),
    _r("destructive-command", "dangerous-command", H, 45,
       r"\brm\s+-[a-z]*r[a-z]*f?[a-z]*\s+(/|~|\$HOME|\*)(\s|$)|\bmkfs\b|:\(\)\s*\{\s*:\|:&\s*\}\s*;:|\bdd\s+if=.{0,40}of=/dev/"),
    _r("reverse-shell", "dangerous-command", C, 60,
       r"/dev/tcp/|\bnc\s+(-[a-z]*e|-[a-z]*c)\b|\bbash\s+-i\b|\bsocat\b.{0,40}\bexec\b"),
    # --- Pushing the agent beyond the ticket
    _r("weaken-checks", "scope-escalation", M, 20,
       r"\b(disable|skip|remove|delete|comment out|turn off)\b.{0,20}\b(tests?|ci|checks?|linters?|branch protection|codeowners|security (scan|check)s?|code review)\b"),
    _r("push-protected", "scope-escalation", M, 20,
       r"\bforce[- ]push\b|\b(push|commit)\b.{0,15}\b(directly\s+)?(to\s+)?(main|master|production|prod)\b"),
    _r("self-approve", "scope-escalation", H, 35,
       r"\b(approve|merge)\b.{0,15}\b(this|your own|the)\b.{0,10}\b(pr|pull request)\b|\badd\b.{0,20}\b(as a )?(collaborator|admin|maintainer)\b"),
)
