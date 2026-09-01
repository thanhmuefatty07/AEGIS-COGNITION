"""Setuptools compatibility hook for stale generated build output.

The repository may contain a previous ``build/lib`` staging tree.  Setuptools
copies that tree into the next wheel unless the nested staging directory is
removed after the current sources are materialized.  Only the generated
``build/lib/build`` child is removed; source files and the checkout's existing
build directory are left untouched.
"""

from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py


class _CleanStagingBuildPy(_build_py):
    def run(self) -> None:
        super().run()
        nested_staging = Path(self.build_lib) / "build"
        if nested_staging.is_dir():
            shutil.rmtree(nested_staging)


setup(cmdclass={"build_py": _CleanStagingBuildPy})
