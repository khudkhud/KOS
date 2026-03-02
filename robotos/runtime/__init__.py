"""Runtime assembly and scenario wiring modules."""

from robotos.runtime.bootstrap import BuildOptions, EmbodimentProfile, build, build_http_app, build_system
from robotos.runtime.demo_wiring import run_demo

__all__ = ["BuildOptions", "EmbodimentProfile", "build", "build_http_app", "build_system", "run_demo"]
