"""Optionally stage an audited uftrace prefix into a Linux platform wheel."""
import os
import importlib.util
from pathlib import Path
import shutil

from setuptools import Distribution, setup
from setuptools.command.build_py import build_py
from wheel.bdist_wheel import bdist_wheel


def native_tools():
    specification = importlib.util.spec_from_file_location('uftrace_dist', Path(__file__).parent / 'scripts/uftrace_dist.py')
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class NativeDistribution(Distribution):
    def has_ext_modules(self):
        return bool(os.environ.get('FG_UFTRACE_PREFIX'))


class BuildPy(build_py):
    def run(self):
        super().run()
        destination = Path(self.build_lib) / 'fglib' / '_native'
        if destination.exists():
            shutil.rmtree(destination)
        prefix = os.environ.get('FG_UFTRACE_PREFIX')
        if prefix:
            native_tools().stage_payload(Path(prefix), destination / 'uftrace')


class NativeWheel(bdist_wheel):
    def get_tag(self):
        if os.environ.get('FG_UFTRACE_PREFIX'):
            return 'py3', 'none', 'linux_' + native_tools().host_architecture()
        return super().get_tag()


setup(distclass=NativeDistribution, cmdclass={'build_py': BuildPy, 'bdist_wheel': NativeWheel})
