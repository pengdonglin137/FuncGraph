"""Configuration files and presets for the command line.

    # funcgraph.ini  (found via --config, ./funcgraph.ini or ~/.config/funcgraph/config.ini)
    [default]
    fast = true
    filter = true
    enable-fold = true

    [preset pi4]
    vmlinux = /vol_1t/raspi/linux/vmlinux
    kernel-src = /vol_1t/raspi/linux
    source-browser = github
    source-browser-rev = v6.18
    cross-compile = aarch64-linux-gnu-
    timeline = true

Keys are the long option names. [default] always applies, `--preset NAME` adds
[preset NAME] on top, and options given on the command line win over both.
`cross-compile` / `llvm` set the CROSS_COMPILE / LLVM environment variables unless
they are already set. List options take whitespace-separated values
(`module-dirs = a b`); comma lists (`pid = 1,2`) work as on the command line.
"""
import argparse
import configparser
import os
import shlex

SEARCH = ('funcgraph.ini', os.path.join(os.path.expanduser('~'), '.config', 'funcgraph', 'config.ini'))
ENV_KEYS = {'cross-compile': 'CROSS_COMPILE', 'llvm': 'LLVM'}
TRUE = {'1', 'yes', 'true', 'on'}
FALSE = {'0', 'no', 'false', 'off'}


class ConfigError(Exception):
    pass


def find_config(explicit=None):
    if explicit:
        if not os.path.isfile(explicit):
            raise ConfigError(f"config file '{explicit}' not found")
        return explicit
    for p in SEARCH:
        if os.path.isfile(p):
            return p
    return None


def load(path, preset=None):
    """Return (dict key->str, list of section names applied)."""
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=('#', ';'))
    cp.optionxform = str
    with open(path, encoding='utf-8') as f:
        cp.read_file(f)
    values, used = {}, []
    if cp.has_section('default'):
        values.update(cp['default'])
        used.append('default')
    if preset:
        sec = 'preset ' + preset
        if not cp.has_section(sec):
            names = [s[7:] for s in cp.sections() if s.startswith('preset ')]
            raise ConfigError(f"preset '{preset}' not in {path} (available: {', '.join(names) or 'none'})")
        values.update(cp[sec])
        used.append(sec)
    return values, used


def apply(parser, values, source=''):
    """Turn config values into parser defaults (command line still overrides)."""
    actions = {a.option_strings[0][2:]: a for a in parser._actions if a.option_strings and a.option_strings[0].startswith('--')}
    defaults = {}
    for key, raw in values.items():
        key = key.strip().lower().replace('_', '-')
        if key in ENV_KEYS:
            os.environ.setdefault(ENV_KEYS[key], raw.strip())
            continue
        a = actions.get(key)
        if a is None:
            raise ConfigError(f"{source}: unknown option '{key}'")
        v = raw.strip()
        if isinstance(a, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
            if v.lower() not in TRUE | FALSE:
                raise ConfigError(f"{source}: '{key}' expects true/false, got '{v}'")
            on = v.lower() in TRUE
            defaults[a.dest] = on if isinstance(a, argparse._StoreTrueAction) else not on
        elif isinstance(a, argparse._AppendAction):
            defaults[a.dest] = shlex.split(v)
        elif a.nargs in ('*', '+'):
            items = shlex.split(v)
            defaults[a.dest] = [a.type(x) if a.type else x for x in items]
        else:
            try:
                defaults[a.dest] = a.type(v) if a.type else v
            except (ValueError, argparse.ArgumentTypeError) as e:
                raise ConfigError(f"{source}: bad value for '{key}': {e}")
    parser.set_defaults(**defaults)
    return defaults
