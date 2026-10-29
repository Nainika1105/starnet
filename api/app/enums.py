from enum import StrEnum


class Category(StrEnum):
    POTHOLE = "POTHOLE"
    STREETLIGHT = "STREETLIGHT"
    WATER_LEAK = "WATER_LEAK"
    GARBAGE = "GARBAGE"
    TREE = "TREE"
    FOOTPATH = "FOOTPATH"


class IssueStatus(StrEnum):
    REPORTED = "REPORTED"
    VERIFIED = "VERIFIED"
    ASSIGNED = "ASSIGNED"
    RESOLVED = "RESOLVED"
    CONFIRMED = "CONFIRMED"
    REOPENED = "REOPENED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class Role(StrEnum):
    DISCOVERER = "DISCOVERER"
    CORROBORATOR = "CORROBORATOR"
    VERIFIER = "VERIFIER"
    CONFIRMER = "CONFIRMER"


class VestingState(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    PENDING_RESOLUTION = "PENDING_RESOLUTION"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    PAID = "PAID"
    FORFEITED = "FORFEITED"


class Stage(StrEnum):
    """Vesting stage a ledger entry belongs to."""

    VERIFICATION = "VERIFICATION"
    RESOLUTION = "RESOLUTION"
    CONFIRMATION = "CONFIRMATION"
    NONE = "NONE"


class EntryType(StrEnum):
    """Ledger entry kinds. Balance is the signed sum over all entries."""

    VEST = "VEST"              # positive: points vested to the user
    FORFEIT = "FORFEIT"        # zero-amount marker: unvested remainder cancelled
    REVERSAL = "REVERSAL"      # negative: claws back a vesting invalidated by a reopen
    REDEMPTION = "REDEMPTION"  # negative: points spent in the catalogue


class TaskType(StrEnum):
    VERIFY = "VERIFY"
    CONFIRM = "CONFIRM"


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    # The task stopped being needed before anyone actioned it -- e.g. a verify
    # task on an issue that corroboration carried over theta_hi by itself.
    # Distinct from EXPIRED so "nobody turned up" and "no longer required" do
    # not read the same in the audit trail.
    SUPERSEDED = "SUPERSEDED"


class Verdict(StrEnum):
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class Tier(StrEnum):
    NEW = "NEW"
    TRUSTED = "TRUSTED"
    VERIFIER_ELIGIBLE = "VERIFIER_ELIGIBLE"
