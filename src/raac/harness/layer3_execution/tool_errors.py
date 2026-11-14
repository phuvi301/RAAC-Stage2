"""Specific, safe text for a failed tool call.

The model and the logs get a short reason chosen from a fixed vocabulary. Raw OS or library
exception text is never forwarded, because it can carry host paths or file contents.
"""

import errno

from raac.common.errors import PatchFormatError, RejectedEditError, SourceModifiedError



class ObservationLookupError(ValueError):
    """read_tool_result could not resolve a reference or path. The message is built by the framework from field
    names only (never report contents), so it is safe to show the model."""


GENERIC = ("InvalidArguments", "Invalid tool arguments or the file is unavailable. Check the path and the arguments.")

# First match wins. Substrings of the framework's own, static messages.
_VALUE_ERRORS = (
    (("container file operation failed",),
     ("ContainerFileError", "The container could not complete the file operation (it failed or hit a limit).")),
    (("outside workspace", "path escape", "symlink", "hardlink", "invalid repository path", "nonregular", "not regular",
      "relative path inside repo"),
     ("PathNotAllowed", "Path is not allowed: outside the repository (for example an absolute /tmp path), a symlink, "
                        "hidden or an invalid name. Use a path relative to the repository root.")),
    (("size limit", "too large", "snapshot limit", "exceeded its limit"),
     ("TooLarge", "The file, output or snapshot exceeds the size limit.")),
    (("not a repository directory",),
     ("BadWorkingDirectory", "cwd must be an existing directory inside the repository.")),
    (("invalid line range",),
     ("InvalidLineRange", "The line range is invalid: start_line must be at least 1 and not after end_line.")),
    (("query must contain",),
     ("InvalidQuery", "The search query must contain 1 to 256 characters.")),
    (("specify nonempty old_text",),
     ("InvalidReplace", "Give a non-empty old_text and a positive expected_occurrences.")),
    (("candidate attribution failed", "unattributed"),
     ("AttributionFailed", "Repository changes could not be attributed to a role, so the candidate was refused.")),
)

_OS_ERRORS = {
    errno.ENOENT: ("NotFound", "File or directory not found. Check the path (list_files shows what exists)."),
    errno.EACCES: ("PermissionDenied", "The operating system denied access to the path."),
    errno.EPERM: ("PermissionDenied", "The operating system denied access to the path."),
    errno.EISDIR: ("IsDirectory", "The path is a directory, not a file."),
    errno.ENOTDIR: ("NotDirectory", "A parent of the path is not a directory."),
}


def describe_tool_error(exc: BaseException) -> dict:
    """Observation fields (error, error_kind, ...) for an exception raised by a workspace tool."""
    if isinstance(exc, SourceModifiedError):
        return {"error": "This command changed repository files that the current role may not change. "
                         "Write build or test artifacts outside the repository (for example under /tmp).",
                "error_kind": "SourceModified", "detail": str(exc), "paths": list(exc.paths), "more_paths": exc.more}
    if isinstance(exc, ObservationLookupError):
        return {"error": str(exc)[:600], "error_kind": "UnknownObservation"}
    if isinstance(exc, RejectedEditError):
        if exc.reason == "invalid_python":
            return {"error": f"No file was written: the new content of {exc.path} is not valid Python "
                             f"(syntax error at line {exc.line}). The file is unchanged; fix the edit and try again.",
                    "error_kind": "InvalidPython", "path": exc.path, "line": exc.line}
        return {"error": f"No file was written: the new content of {exc.path} contains tool-call markup "
                         "(for example 'to=functions.'), which is not part of the source. Send only the text meant "
                         "for the file.", "error_kind": "ToolMarkupInContent", "path": exc.path}
    if isinstance(exc, OSError) and exc.errno is not None:
        kind, text = _OS_ERRORS.get(exc.errno, ("FileOperationFailed", "The file operation failed."))
        return {"error": text, "error_kind": kind}
    message = str(exc)
    if isinstance(exc, PermissionError):
        # Raised by the framework's own role rules, so the message is a static sentence.
        return {"error": message[:300] or "This role may not perform that operation.", "error_kind": "RoleNotAllowed"}
    lowered = message.lower()
    for needles, (kind, text) in _VALUE_ERRORS:
        if any(needle in lowered for needle in needles):
            return {"error": text, "error_kind": kind}
    if isinstance(exc, PatchFormatError):
        return {"error": f"No file was written: the patch was rejected ({message[:200]}). Send a unified diff: per file "
                         "'--- a/path' (or '--- /dev/null' for a new file) and '+++ b/path', then '@@ -start,count "
                         "+start,count @@' hunks whose lines start with ' ', '+' or '-' and match the current file "
                         "exactly. To create or fully rewrite a file, edit_file is simpler.",
                "error_kind": "InvalidPatch"}
    return {"error": GENERIC[1], "error_kind": GENERIC[0]}
