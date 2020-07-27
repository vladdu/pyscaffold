"""
Default PyScaffold's actions and functions to manipulate them.

When generating a project, PyScaffold uses a pipeline of functions (each function will
receive as arguments the values returned by the previous functions). These functions
have an specific purpose and are called **actions**. Please follow the :obj:`Action`
signature when developing your own action.

Note:
    Some actions are more complex and are placed in dedicated modules together with
    other auxiliary functions, see :mod:`pyscaffold.structure`,
    :mod:`pyscaffold.update`.
"""
import os
from datetime import date, datetime
from functools import reduce
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from . import info, repo
from .exceptions import (
    DirectoryAlreadyExists,
    DirectoryDoesNotExist,
    GitDirtyWorkspace,
    InvalidIdentifier,
)
from .identification import (
    deterministic_sort,
    get_id,
    is_valid_identifier,
    make_valid_identifier,
)
from .log import logger
from .structure import Structure, create_structure, define_structure
from .update import version_migration

ScaffoldOpts = Dict[str, Any]
"""Dictionary with PyScaffold's options, see :obj:`pyscaffold.api.create_project`.
Should be treated as immutable (if required, copy before changing).
"""

Action = Callable[[Structure, ScaffoldOpts], Tuple[Structure, ScaffoldOpts]]
"""Signature of a PyScaffold action"""


# -------- Functions that deal with actions --------


def discover(extensions):
    """Retrieve the action list.

    This is done by concatenating the default list with the one generated after
    activating the extensions.

    Args:
        extensions (list): list of functions responsible for activating the
        extensions.

    Returns:
        list: scaffold actions.
    """
    actions = DEFAULT.copy()
    extensions = deterministic_sort(extensions)

    # Activate the extensions
    return reduce(lambda acc, f: _activate(f, acc), extensions, actions)


def invoke(action, struct, opts):
    """Invoke action with proper logging.

    Args:
        struct (dict): project representation as (possibly) nested
            :obj:`dict`.
        opts (dict): given options, see :obj:`create_project` for
            an extensive list.

    Returns:
        tuple(dict, dict): updated project representation and options
    """
    logger.report("invoke", get_id(action))
    with logger.indent():
        struct, opts = action(struct, opts)

    return struct, opts


# -------- PyScaffold's actions --------


def get_default_options(struct, opts):
    """Compute all the options that can be automatically derived.

    This function uses all the available information to generate sensible
    defaults. Several options that can be derived are computed when possible.

    Args:
        struct (dict): project representation as (possibly) nested
            :obj:`dict`.
        opts (dict): given options, see :obj:`create_project` for
            an extensive list.

    Returns:
        dict, dict: project representation and options with default values set

    Raises:
        :class:`~.DirectoryDoesNotExist`: when PyScaffold is told to
            update an nonexistent directory
        :class:`~.GitNotInstalled`: when git command is not available
        :class:`~.GitNotConfigured`: when git does not know user information

    Note:
        This function uses git to determine some options, such as author name
        and email.
    """
    # This function uses information from git, so make sure it is available
    info.check_git()

    project_path = str(opts.get("project_path", ".")).rstrip(os.sep)
    # ^  Strip (back)slash when added accidentally during update
    opts["project_path"] = Path(project_path)
    opts.setdefault("name", opts["project_path"].name)
    opts.setdefault("package", make_valid_identifier(opts["name"]))
    opts.setdefault("author", info.username())
    opts.setdefault("email", info.email())
    opts.setdefault("release_date", date.today().strftime("%Y-%m-%d"))
    # All kinds of derived parameters
    year = datetime.strptime(opts["release_date"], "%Y-%m-%d").year
    opts.setdefault("year", year)
    opts.setdefault(
        "title",
        "=" * len(opts["name"]) + "\n" + opts["name"] + "\n" + "=" * len(opts["name"]),
    )

    # Initialize empty list of all requirements and extensions
    # (since not using deep_copy for the DEFAULT_OPTIONS, better add compound
    # values inside this function)
    opts.setdefault("requirements", list())
    opts.setdefault("extensions", list())
    opts.setdefault("root_pkg", opts["package"])
    opts.setdefault("qual_pkg", opts["package"])
    opts.setdefault("pretend", False)

    # Save cli params for later updating
    extensions = set(opts.get("cli_params", {}).get("extensions", []))
    args = opts.get("cli_params", {}).get("args", {})
    for extension in opts["extensions"]:
        extensions.add(extension.name)
        if extension.args is not None:
            args[extension.name] = extension.args
    opts["cli_params"] = {"extensions": list(extensions), "args": args}

    return struct, opts


def verify_options_consistency(struct, opts):
    """Perform some sanity checks about the given options.

    Args:
        struct (dict): project representation as (possibly) nested
            :obj:`dict`.
        opts (dict): given options, see :obj:`create_project` for
            an extensive list.

    Returns:
        dict, dict: updated project representation and options
    """
    if not is_valid_identifier(opts["package"]):
        raise InvalidIdentifier(
            f"Package name {opts['package']!r} is not a valid identifier."
        )

    if opts["update"] and not opts["force"]:
        if not info.is_git_workspace_clean(opts["project_path"]):
            raise GitDirtyWorkspace

    return struct, opts


def verify_project_dir(struct, opts):
    """Check if PyScaffold can materialize the project dir structure.

    Args:
        struct (dict): project representation as (possibly) nested
            :obj:`dict`.
        opts (dict): given options, see :obj:`create_project` for
            an extensive list.

    Returns:
        dict, dict: updated project representation and options
    """
    if opts["project_path"].exists():
        if not opts["update"] and not opts["force"]:
            raise DirectoryAlreadyExists(
                "Directory {dir} already exists! Use the `update` option to "
                "update an existing project or the `force` option to "
                "overwrite an existing directory.".format(dir=opts["project_path"])
            )
    elif opts["update"]:
        raise DirectoryDoesNotExist(
            "Project {path} does not exist and thus cannot be "
            "updated!".format(path=opts["project_path"])
        )

    return struct, opts


def init_git(struct, opts):
    """Add revision control to the generated files.

    Args:
        struct (dict): project representation as (possibly) nested
            :obj:`dict`.
        opts (dict): given options, see :obj:`create_project` for
            an extensive list.

    Returns:
        dict, dict: updated project representation and options
    """
    if not opts["update"] and not repo.is_git_repo(opts["project_path"]):
        repo.init_commit_repo(
            opts["project_path"], struct, log=True, pretend=opts.get("pretend")
        )

    return struct, opts


DEFAULT = [
    get_default_options,
    verify_options_consistency,
    define_structure,
    verify_project_dir,
    version_migration,
    create_structure,
    init_git,
]


# -------- Auxiliary functions --------


def _activate(extension, actions):
    """Activate extension with proper logging."""
    logger.report("activate", extension.__module__)
    with logger.indent():
        actions = extension(actions)

    return actions