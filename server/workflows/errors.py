"""Typed failures exposed by the Workflow Control Plane."""


class WorkflowError(Exception):
    """Base class for deterministic Workflow command failures."""


class UnknownWorkflowKindError(WorkflowError):
    """The application registry does not recognize a Workflow Kind."""


class UnknownWorkflowJobKindError(WorkflowError):
    """The application registry does not recognize a Workflow Job Kind."""


class InvalidWorkflowProposalError(WorkflowError):
    """A proposed Workflow violates its versioned Kind contract."""


class WorkflowAuthorizationError(WorkflowError):
    """The current Party lacks authority for the requested command."""


class WorkflowNotFoundError(WorkflowError):
    """The requested Workflow is absent or unavailable to the caller."""
