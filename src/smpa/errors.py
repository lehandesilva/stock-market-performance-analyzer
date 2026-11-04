"""Errors that stop the pipeline."""


class PipelineStop(Exception):
    """A step hit something it can't safely handle. Logged, then the run stops."""
