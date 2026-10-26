"""Repository tools: preserve the project's layout and edit its real workspace."""

# Lớp này nhận PROJECT/repo/ và PROJECT/requirement/user_chat.txt.
# Bên trong repo là cây thư mục thật của phần mềm, không có tên source cố định.
# File tools và lệnh phát triển chạy trên laptop; phạm vi hiện tại không có sandbox.
# Baseline là snapshot đầu run trong RAM; current là nội dung repo hiện tại.

import difflib  # So sánh các dòng văn bản để sinh unified diff nhiều file.
import base64
from copy import deepcopy
import hashlib  # Tính SHA-256 để định danh nội dung file và baseline.
import json  # Chuyển manifest thành JSON trước khi tính hash tổng.
import os  # Duyệt cây thư mục bằng os.walk().
import subprocess  # Hỏi git những file untracked bị .gitignore loại (chỉ đọc).
from pathlib import Path  # Biểu diễn đường dẫn, ghép path và đọc/ghi file.

from .patching import apply_repo_patch, rebase_repo_patch  # Áp patch vào dữ liệu trong RAM, chưa ghi ổ đĩa.
# excluded_directory: thư mục bỏ qua; repo_path: kiểm tra tên; test_path: đoán file test theo tên.
from .paths import excluded_directory, excluded_runtime_name, repo_path, test_path
from raac.common.errors import WorkspaceSnapshotError


class EditConflictError(ValueError):
    """Exact-match edit rejected before writing; the caller must re-read."""

    def __init__(self, actual, expected):
        self.actual = actual
        self.expected = expected
        super().__init__("Text match count differs from expected_occurrences")


class WorkspaceTools:
    # Đây là giới hạn tài nguyên của tool, không phải quy ước cấu trúc project.
    max_file_bytes = 4 * 1024 * 1024  # Tối đa 4 MiB cho từng file.
    max_repo_bytes = 64 * 1024 * 1024  # Tối đa 64 MiB cho các file trong snapshot.
    max_files = 2000  # Tối đa 2.000 file sau khi loại trừ cache/credential.

    def invalidate_runtime_snapshot(self):
        self._checkpoint_fingerprint = None

    def snapshot_runtime(self, *, refresh=True):
        """Explicit JSON state for same-workspace resume; never executor objects."""
        container = getattr(self.executor, "container", None)
        identity = {"mode": getattr(self, "execution_mode", "local"), "root": str(self.root),
                    "container": getattr(container, "id", None), "requirement": self.requirement}
        fingerprint = getattr(self, "_checkpoint_fingerprint", None)
        if refresh or identity["mode"] != "docker" or fingerprint is None:
            current = self._snapshot()
            current_modes = getattr(self, "current_modes", {})
            fingerprint = hashlib.sha256(json.dumps([
                (name, hashlib.sha256(data).hexdigest(), current_modes.get(name))
                for name, data in sorted(current.items())]).encode()).hexdigest()
            self._checkpoint_fingerprint = fingerprint
        cached = getattr(self, "_encoded_baseline", None)
        if cached is None or cached[0] != self.baseline_files:
            cached = (dict(self.baseline_files), {name: base64.b64encode(data).decode()
                                                for name, data in self.baseline_files.items()})
            self._encoded_baseline = cached
        return {"version": 1, "identity": identity, "fingerprint": fingerprint,
                "baseline_ref": self.baseline_ref,
                "baseline_files": dict(cached[1]),
                "coder_paths": sorted(self.coder_paths), "tester_paths": sorted(self.tester_paths),
                "code_edit_count": self.code_edit_count, "test_spec": deepcopy(self.test_spec),
                "environment": {name: deepcopy(getattr(self.environment, name, None)) for name in
                    ("configuration", "verification", "revision", "ready", "smoke_report", "observation_ref")},
                "executor_environment": deepcopy(getattr(self.executor, "environment", None)),
                "docker": {name: deepcopy(getattr(self, name, None)) for name in
                    ("baseline_modes", "setup_generated_paths", "integrity_error", "active_role")
                    if name != "setup_generated_paths"},
                "setup_generated_paths": sorted(getattr(self, "setup_generated_paths", set()))}

    def restore_runtime(self, snapshot):
        """Validate actual content before restoring bookkeeping, never rewrite source."""
        current = self.snapshot_runtime()
        if (snapshot.get("version") != 1 or snapshot.get("identity") != current["identity"]
                or snapshot.get("fingerprint") != current["fingerprint"]):
            raise ValueError("resume_workspace_mismatch")
        encoded = snapshot["baseline_files"]
        if not isinstance(encoded, dict) or len(encoded) > self.max_files:
            raise ValueError("resume_baseline_limit")
        baseline = {}
        for name, data in encoded.items():
            repo_path(name)
            if not isinstance(data, str) or len(data) > (self.max_file_bytes + 2) * 4 // 3:
                raise ValueError("resume_baseline_limit")
            baseline[name] = base64.b64decode(data, validate=True)
        if any(len(data) > self.max_file_bytes for data in baseline.values()) or sum(map(len, baseline.values())) > self.max_repo_bytes:
            raise ValueError("resume_baseline_limit")
        for key in ("coder_paths", "tester_paths", "setup_generated_paths"):
            for name in snapshot[key]:
                repo_path(name)
        if type(snapshot["code_edit_count"]) is not int or snapshot["code_edit_count"] < 0:
            raise ValueError("resume_runtime_invalid")
        self.baseline_files = baseline
        self.baseline_ref = snapshot["baseline_ref"]
        self.coder_paths, self.tester_paths = set(snapshot["coder_paths"]), set(snapshot["tester_paths"])
        self.code_edit_count, self.test_spec = snapshot["code_edit_count"], deepcopy(snapshot["test_spec"])
        for name in ("configuration", "verification", "revision", "ready", "smoke_report", "observation_ref"):
            setattr(self.environment, name, deepcopy(snapshot["environment"][name]))
        if self.executor is not None:
            self.executor.environment = deepcopy(snapshot["executor_environment"])
        if getattr(self, "execution_mode", "local") == "docker":
            for name in ("baseline_modes", "integrity_error", "active_role"):
                value = snapshot["docker"].get(name)
                if value is not None:
                    setattr(self, name, deepcopy(value))
            self.setup_generated_paths = set(snapshot["setup_generated_paths"])

    # project là thư mục cha chứa repo/ và requirement/; executor chạy lệnh local.
    def __init__(self, project: Path, executor):
        self.project = Path(project).resolve()
        self.executor = executor
        self.root = self._checked_path(self.project / "repo", self.project)         # Ghép PROJECT/repo rồi kiểm tra path/symlink; đây là gốc codebase.
        requirements = self._checked_path(self.project / "requirement", self.project)# Requirement nằm ngoài repo nhưng vẫn thuộc thư mục project đã chọn.
        
        # Thiếu cấu trúc đầu vào thì dừng, không phục hồi từ kết quả run cũ.
        if not self.root.is_dir() or not requirements.is_dir():         
            raise ValueError("Project must contain repo/ and requirement/ directories")
        user_chat = self._checked_path(requirements / "user_chat.txt", self.project)
        self.requirement_files = {"requirement/user_chat.txt": user_chat}
        # Đọc yêu cầu UTF-8, có kiểm tra giới hạn dung lượng; thiếu file sẽ báo lỗi.
        self.requirement = self._read(user_chat)
        # strip() chỉ dùng kiểm tra rỗng; nội dung gốc trong self.requirement không đổi.
        if not self.requirement.strip():
            raise ValueError("Requirements must not be empty")
        # Chụp repo đầu run vào RAM: {đường_dẫn_tương_đối: bytes}, gồm cả binary asset.
        self.baseline_files = self._snapshot()

        # Hai set ghi nhận đường dẫn mỗi role đã sửa/xóa; tên không bị lưu trùng.
        self.coder_paths = set()
        self.tester_paths = set()
        # Đếm thao tác Coder thành công, không phải số file khác nhau.
        self.code_edit_count = 0
        self.test_spec = None
        from ..environment import EnvironmentManager
        self.environment = EnvironmentManager(self)

        # Sắp xếp tên ổn định rồi tạo danh sách (tên file, SHA-256 nội dung).
        manifest = [(name, hashlib.sha256(data).hexdigest()) for name, data in sorted(self.baseline_files.items())]
        # Hash cả manifest lẫn yêu cầu: đổi code hoặc requirement đều đổi định danh.
        # json.dumps tạo chuỗi; \0 phân cách hai phần; encode tạo bytes cho SHA-256.
        # hexdigest() trả chuỗi hex; tiền tố snapshot: nói rõ loại baseline.
        self.baseline_ref = "snapshot:" + hashlib.sha256(
            (json.dumps(manifest) + "\0" + self.requirement).encode("utf-8")).hexdigest()

    @staticmethod
    def _checked_path(path: Path, boundary: Path) -> Path:
        if not path.is_relative_to(boundary):
            raise ValueError("Path is outside workspace")
        # Duyệt ngược từ file/thư mục đích tới giới hạn để kiểm tra từng thành phần.
        current = path
        while current != boundary:
            if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
                raise ValueError("Source file symlinks/path escapes are not allowed")
            current = current.parent
        # Sau resolve, đích thực tế cũng phải còn nằm trong giới hạn.
        if not path.resolve().is_relative_to(boundary):
            raise ValueError("Path is outside workspace")
        # Trả path đã kiểm tra; thao tác này không khóa filesystem trước thay đổi đồng thời.
        return path

    # Dấu * buộc allow_root phải truyền bằng tên, ví dụ allow_root=True.
    def _path(self, name: str, *, allow_root=False) -> Path:
        # Chuẩn hóa tên tương đối; chặn absolute path, .., credential và tên bị loại trừ.
        name = repo_path(name, allow_root=allow_root)
        # '.' là chính repo; tên khác ghép dưới repo. Boundary kiểm tra là project,
        # còn repo_path và cách ghép self.root giới hạn đường dẫn tool trong repo/.
        return self._checked_path(self.root if name == "." else self.root / name, self.project)

    # classmethod nhận lớp qua cls để dùng giới hạn max_file_bytes của lớp.
    @classmethod
    def _read(cls, path: Path) -> str:
        # stat().st_size là dung lượng byte trên filesystem, kiểm tra trước khi đọc.
        if path.stat().st_size > cls.max_file_bytes:
            raise ValueError("File exceeds size limit")
        # UTF-8-sig đọc UTF-8 và bỏ BOM đầu file nếu có; binary không decode được sẽ lỗi.
        return path.read_text(encoding="utf-8-sig")

    # File sinh ra khi cài đặt (build/, *.pyd, .c do Cython sinh, egg-info) nằm trong .gitignore.
    # Đó không phải source: agent không sửa chúng và chúng có thể làm repo phình gấp đôi sau khi cài.
    # Repo git có thể nằm ở thư mục con của repo/ (vd repo/astropy), nên tìm .git sâu tối đa 2 cấp.
    # Không có git hoặc git lỗi thì trả tập rỗng: snapshot quay về hành vi cũ, không đoán.
    def _git_ignored(self) -> set[str]:
        key = lambda path: os.path.normcase(os.path.normpath(str(path)))  # noqa: E731
        roots = []
        for directory, dirs, names in os.walk(self.root, followlinks=False):
            if ".git" in dirs or ".git" in names:
                roots.append(Path(directory))
            depth = len(Path(directory).relative_to(self.root).parts)
            dirs[:] = [name for name in dirs if name != ".git" and not excluded_directory(name)] if depth < 2 else []
        ignored = set()
        for root in roots:
            try:
                done = subprocess.run(
                    ["git", "-C", str(root), "ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z"],
                    capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                continue
            if done.returncode:
                continue
            for item in done.stdout.decode("utf-8", errors="replace").split("\0"):
                if item:
                    ignored.add(key(root / item.rstrip("/")))
        return ignored

    # Mỗi lần gọi đọc lại repo hiện tại; baseline_files chỉ được gán lúc khởi tạo.
    def _snapshot(self) -> dict[str, bytes]:
        ignored = self._git_ignored()
        ignore_key = lambda path: os.path.normcase(os.path.normpath(str(path)))  # noqa: E731
        # files chứa snapshot; total đếm tổng byte của các file được chọn.
        files, total = {}, 0
        # os.walk trả thư mục hiện tại, danh sách thư mục con và tên file.
        # followlinks=False không tự duyệt vào symlink thư mục.
        for directory, dirs, names in os.walk(self.root, followlinks=False):
            # Chỉ giữ các thư mục con hợp lệ để os.walk đi tiếp.
            kept = []
            # Sắp xếp giúp thứ tự xử lý ổn định giữa các lần chạy.
            for name in sorted(dirs):
                # So tên không phân biệt hoa/thường với danh sách cache/VCS bị loại trừ.
                if excluded_directory(name):
                    continue
                # Tạo Path thư mục con rồi kiểm tra symlink/junction/path escape.
                candidate = Path(directory) / name
                # Thư mục bị .gitignore loại (build/, egg-info...) không thuộc snapshot.
                if ignore_key(candidate) in ignored:
                    continue
                self._checked_path(candidate, self.project)
                # Thư mục qua kiểm tra được giữ lại cho bước duyệt đệ quy.
                kept.append(name)
            # Sửa list tại chỗ để điều khiển os.walk; 'dirs = kept' không có tác dụng này.
            dirs[:] = kept
            # Duyệt các file của thư mục hiện tại theo thứ tự tên.
            for name in sorted(names):
                # Ghép đường dẫn đầy đủ tới file.
                path = Path(directory) / name
                # File untracked bị .gitignore loại (.pyd, .c sinh ra...) không thuộc snapshot.
                if ignore_key(path) in ignored:
                    continue
                # Chuyển thành tên dưới repo, dùng '/' nhất quán cả trên Windows.
                relative = path.relative_to(self.root).as_posix()
                try:
                    # Áp quy tắc tên file chung, gồm loại credential và đường dẫn cấm.
                    repo_path(relative)
                except ValueError:
                    # File bị loại trừ không được đọc hoặc đưa vào snapshot.
                    continue
                # Kiểm tra đích filesystem trước khi đọc dữ liệu.
                self._checked_path(path, self.project)
                # Lấy kích thước file theo byte.
                size = path.stat().st_size
                if size > self.max_file_bytes:
                    raise WorkspaceSnapshotError("workspace_file_too_large")
                # Cộng file hiện tại trước khi kiểm tra tổng dung lượng.
                total += size
                # len(files) đếm file đã thêm; >= max_files chặn thêm file vượt trần.
                if len(files) >= self.max_files or total > self.max_repo_bytes:
                    raise WorkspaceSnapshotError("workspace_snapshot_limit")
                # Đọc nguyên bytes để giữ được cả source, test và binary asset.
                files[relative] = path.read_bytes()
        # Snapshot nằm trong RAM, không tạo thư mục workspace hoặc artifact kết quả.
        return files

    # property cho phép dùng workspace.readable thay vì workspace.readable().
    @property
    def readable(self):
        # set(dict) lấy các key: tên file trong snapshot hiện tại, không lấy nội dung.
        # Có thể gồm binary; không đảm bảo tất cả đều đọc được bằng read_file().
        return set(self._snapshot())

    # Hàm chỉ kiểm tra quyền; chạy hết không lỗi nghĩa là cho phép ghi/xóa.
    def _can_write(self, role, name):
        # Các role Analyst/Planner/Reviewer không có quyền ghi ở lớp này.
        if role not in {"Coder", "Tester"}:
            raise PermissionError("Read-only role")
        # Tester bị chặn nếu file đã được Coder chạm vào, hoặc file có từ baseline
        # nhưng không thuộc tester_paths và không được nhận diện là test theo tên.
        # File hoàn toàn mới được phép tạo; hàm này không phân tích nó có thật là test.
        if role == "Tester" and (name in self.coder_paths or (
                name in self.baseline_files and name not in self.tester_paths and not test_path(name))):
            raise PermissionError("Tester may add tests or update existing test files, not implementation")
        if name in self.baseline_files:
            # Thử decode dữ liệu cũ; lỗi Unicode sẽ chặn ghi/xóa binary baseline.
            self.baseline_files[name].decode("utf-8-sig")

    # Chuyển baseline bytes sang văn bản để tạo hoặc áp patch văn bản.
    def _baseline_text(self):
        result = {}
        for name, data in self.baseline_files.items():
            try:
                # Bỏ BOM và chuẩn hóa CRLF Windows thành LF để so sánh nhất quán.
                result[name] = data.decode("utf-8-sig").replace("\r\n", "\n")
            except UnicodeError:
                # File không decode được vẫn còn trong baseline_files, chỉ không có ở result.
                continue
        return result

    # Sinh diff tổng hợp từ đầu run tới hiện tại, chỉ cho các đường dẫn Coder chạm vào.
    def current_patch(self) -> str:
        current = self._snapshot()
        # Thu từng mảnh diff rồi nối thành một patch nhiều file.
        chunks = []
        for name in sorted(self.coder_paths):
            # .get() trả None nếu file không có ở baseline hoặc đã bị xóa hiện tại.
            before = self.baseline_files.get(name)
            after = current.get(name)
            # Coder có thể sửa rồi phục hồi nguyên bản; trường hợp đó không có diff.
            if before == after:
                continue
            # Chuyển dữ liệu cũ/mới thành văn bản LF; phía không có file dùng chuỗi rỗng.
            old = before.decode("utf-8-sig").replace("\r\n", "\n") if before is not None else ""
            new = after.decode("utf-8-sig").replace("\r\n", "\n") if after is not None else ""
            # a/ là phía trước, b/ là phía sau; /dev/null biểu thị không tồn tại trong diff.
            # Đây là ký hiệu định dạng, không truy cập thiết bị /dev/null trên host.
            old_name = f"a/{name}" if before is not None else "/dev/null"
            new_name = f"b/{name}" if after is not None else "/dev/null"
            # Tách dòng có giữ newline, tạo unified diff với header tên file hai phía.
            lines = list(difflib.unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                            fromfile=old_name, tofile=new_name))
            if not lines:
                # Tạo/xóa file rỗng không sinh diff nội dung; dùng hunk rỗng parser hỗ trợ.
                lines = [f"--- {old_name}\n", f"+++ {new_name}\n", "@@ -0,0 +0,0 @@\n"]
            # Dòng không có newline cuối cần marker riêng để parser tái dựng đúng nội dung.
            chunks.extend(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)
        # Ghép các file thành một chuỗi; không đổi code thì kết quả có thể là ''.
        patch = "".join(chunks)
        if patch:
            # Tự kiểm chứng: áp patch vừa tạo vào baseline trong RAM.
            checked = apply_repo_patch(self._baseline_text(), patch)
            for name, content in checked.items():
                # So kết quả tái dựng với file hiện tại, cùng cách chuẩn hóa xuống dòng.
                actual = current.get(name)
                if (actual.decode("utf-8-sig").replace("\r\n", "\n") if actual is not None else None) != content:
                    raise ValueError("Generated patch does not match saved files")
        # Ví dụ Coder sửa 1→2 rồi 2→3, patch trả về thể hiện baseline 1→current 3.
        return patch


    def patch_matches_current(self, patch: str) -> bool:
        """Recognize an already-applied diff without writing or trusting its old baseline."""
        try:
            current = {}
            for name, data in self._snapshot().items():
                try:
                    current[name] = data.decode("utf-8-sig").replace("\r\n", "\n")
                except UnicodeError:
                    continue
            undone = apply_repo_patch(current, patch, reverse=True)
            for name in set(undone) - self.coder_paths:
                # A previous run's changes may already be the entire new run's
                # baseline. Recognize them as no-op, without attributing them to
                # this Coder or accepting unrelated external modifications.
                data = self.baseline_files.get(name)
                baseline = data.decode("utf-8-sig").replace("\r\n", "\n") if data is not None else None
                if current.get(name) != baseline:
                    return False
            before = dict(current)
            for name, content in undone.items():
                if content is None:
                    before.pop(name, None)
                else:
                    before[name] = content
            replayed = apply_repo_patch(before, patch)
            return all(current.get(name) == content for name, content in replayed.items())
        except (ValueError, OSError):
            return False

    def select_coder_patch(self, patch, *, git_cwds=()):
        """Return a uniquely applicable patch, or a verified already-saved no-op."""
        candidates = [patch]
        for cwd in git_cwds:
            directory = Path(cwd).resolve()
            if not directory.is_relative_to(self.root):
                continue
            while directory.is_relative_to(self.root):
                if (directory / ".git").exists():
                    prefix = directory.relative_to(self.root).as_posix()
                    if prefix != ".":
                        rebased = rebase_repo_patch(patch, prefix)
                        if rebased not in candidates:
                            candidates.append(rebased)
                    break
                if directory == self.root:
                    break
                directory = directory.parent
        for candidate in candidates:
            if self.patch_matches_current(candidate):
                return candidate, True
        applicable = []
        for candidate in candidates:
            try:
                changes = apply_repo_patch(self._baseline_text(), candidate)
                for name in changes:
                    self._path(name)
                    self._can_write("Coder", name)
            except (ValueError, OSError, UnicodeError):
                continue
            applicable.append(candidate)
        if len(applicable) != 1:
            raise ValueError("Coder patch has no unique validated workspace base")
        return applicable[0], False

    def python_candidates(self, limit=20):
        """Discover local runtime paths without scanning packages or running code.

        Candidates are hints only; Environment must verify startup/imports/tests.
        Ignore symlink/junction directories and preserve executable symlink identity.
        """
        candidates = []
        for directory, dirs, _ in os.walk(self.root, followlinks=False):
            kept = []
            for name in sorted(dirs):
                path = Path(directory) / name
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    continue
                if excluded_runtime_name(name) or name.lower() == "venv":
                    for relative in ("Scripts/python.exe", "bin/python", "python.exe", "bin/python3"):
                        executable = path / relative
                        if executable.is_file():
                            candidates.append(str(executable.absolute()))
                            if len(candidates) >= limit:
                                return candidates
                            break
                elif not excluded_directory(name):
                    kept.append(name)
            dirs[:] = kept
        return candidates

    # Liệt kê đường dẫn và dung lượng các file trong repo; hỗ trợ chọn thư mục, đệ quy, giới hạn kết quả.
    def list_files(self, path=".", recursive=True, limit=200):
        parent = self._path(path, allow_root=True)
        if not parent.is_dir():
            raise ValueError("Not a repository directory")
        prefix = "" if path == "." else repo_path(path) + "/"
        entries = []
        for name, data in sorted(self._snapshot().items()):
            if not name.startswith(prefix) or (not recursive and "/" in name[len(prefix):]):
                continue
            entries.append({"path": name, "bytes": len(data)})
        return {"files": entries[:limit], "total": len(entries), "truncated": len(entries) > limit}

    # Đọc nội dung file hoặc một khoảng dòng. 
    # Start_line/end_line dùng số dòng từ 1; end_line=None nghĩa là tới hết file.
    def read_file(self, path: str, start_line=1, end_line=None) -> dict:
        # User chat đã nằm trong request của role; file tools chỉ truy cập repo/.
        target = self._path(path)
        # Đọc văn bản có kiểm tra dung lượng, rồi tách dòng và giữ ký tự xuống dòng.
        text = self._read(target)
        lines = text.splitlines(keepends=True)
        # Từ chối dòng bắt đầu < 1 hoặc kết thúc nằm trước bắt đầu.
        if start_line < 1 or (end_line is not None and end_line < start_line):
            raise ValueError("Invalid line range")
        # Python đánh index từ 0, slice không lấy phần tử cuối: dòng 10–20 là [9:20].
        content = "".join(lines[start_line - 1:end_line])
        # Cắt tối đa 131072 ký tự (không phải byte); total_lines đếm toàn bộ file.
        return {"path": repo_path(path), "content": content[:131072], "total_lines": len(lines),
                "truncated": len(content) > 131072}

    # Tìm chuỗi trong các file văn bản, trả tên file và số dòng.
    # Tìm hàm, class, API hoặc nơi sử dụng một thành phần.
    def search_repo(self, query: str) -> dict:
        # Query phải có nội dung và không dài quá 256 ký tự.
        if not query or len(query) > 256:
            raise ValueError("Query must contain 1..256 characters")
        matches = []
        for name, data in sorted(self._snapshot().items()):
            try:
                # Chỉ tìm trong file decode được thành văn bản UTF-8.
                content = data.decode("utf-8-sig")
            except UnicodeError:
                continue
            # Nội dung chứa null thường là binary; quy tắc đơn giản này bỏ qua nó.
            if "\0" in content:
                continue
            # enumerate(..., 1) cung cấp số dòng bắt đầu từ 1.
            for number, line in enumerate(content.splitlines(), 1):
                # casefold() so sánh Unicode không phân biệt hoa/thường.
                if query.casefold() in line.casefold():
                    # Mỗi kết quả giữ tên file, số dòng và tối đa 500 ký tự dòng đó.
                    matches.append({"path": name, "line": number, "text": line[:500]})
                    # Chạm 100 kết quả thì dừng; không kiểm tra có kết quả thứ 101 không.
                    if len(matches) == 100:
                        return {"matches": matches, "truncated": True}
        # Duyệt xong mà chưa chạm ngưỡng thì kết quả không bị cắt.
        return {"matches": matches, "truncated": False}

    # Tạo file mới hoặc ghi đè toàn bộ nội dung file thật trong repo.
    def edit_file(self, *, role: str, path: str, content: str) -> dict:
        # Chuẩn hóa tên, kiểm tra đường dẫn thật, rồi kiểm tra quyền role.
        name = repo_path(path)
        target = self._path(name)
        self._can_write(role, name)
        # Encode để kiểm tra byte thực tế, không chỉ số ký tự Unicode.
        if len(content.encode("utf-8")) > self.max_file_bytes:
            raise ValueError("File exceeds size limit")
        # Tạo toàn bộ thư mục cha còn thiếu; thư mục đã tồn tại không gây lỗi.
        target.parent.mkdir(parents=True, exist_ok=True)
        # Kiểm tra lại path sau bước tạo thư mục.
        self._path(name)
        # Ghi đè file thật trong repo. newline='\n' tránh tự đổi LF sang CRLF trên Windows.
        # Giới hạn tổng repo được kiểm tra ở snapshot, chưa kiểm tra trước mỗi lần ghi.
        target.write_text(content, encoding="utf-8", newline="\n")
        if role == "Coder":
            # Một file sửa nhiều lần tăng bộ đếm nhiều lần nhưng set chỉ giữ một tên.
            self.code_edit_count += 1
            self.coder_paths.add(name)
        else:
            # _can_write đã loại các role khác, nên nhánh này thuộc Tester.
            self.tester_paths.add(name)
        # Observation trả tên file và hash nội dung vừa ghi, không trả lại toàn bộ code.
        return {"path": name, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}

    def replace_text(self, *, role, path, old_text, new_text, expected_occurrences=1):
        """Sửa vùng văn bản khớp chính xác, giữ quyền role và phần còn lại của file."""
        name = repo_path(path)
        target = self._path(name)
        self._can_write(role, name)
        # File test có tên riêng được phép sửa nếu chính Tester đã tạo trước đó.
        if role == "Tester" and name not in self.tester_paths and not test_path(name):
            raise PermissionError("Tester can replace text only in test files")
        if not old_text or type(expected_occurrences) is not int or not 1 <= expected_occurrences <= 100:
            raise ValueError("Specify nonempty old_text and a positive exact match count")
        before = self._read(target)
        actual = before.count(old_text)
        if actual != expected_occurrences:
            raise EditConflictError(actual, expected_occurrences)
        after = before.replace(old_text, new_text)
        result = self.edit_file(role=role, path=name, content=after)
        return {**result, "replacements": actual,
                "before_sha256": hashlib.sha256(before.encode("utf-8")).hexdigest()}

    # Xóa một file cụ thể sau khi kiểm tra quyền.
    def delete_file(self, *, role: str, path: str):
        # Kiểm tra tên và quyền trước khi thực hiện thao tác filesystem.
        name = repo_path(path)
        self._can_write(role, name)
        # unlink() xóa file thật; file không tồn tại sẽ gây lỗi.
        self._path(name).unlink()
        if role == "Coder":
            # Ghi nhận cả thao tác xóa để current_patch() sinh diff tương ứng.
            self.code_edit_count += 1
            self.coder_paths.add(name)
        else:
            self.tester_paths.add(name)
        # Trả xác nhận xóa thành công.
        return {"path": name, "deleted": True}

    # Áp unified diff để thêm/sửa/xóa nhiều file. Mặc định dựa trên code hiện tại.
    def apply_patch(self, patch: str, base="current") -> dict:
        if base not in {"current", "baseline"}:
            raise ValueError("Unknown patch base")
        if base == "baseline":
            # Dùng nội dung đầu run, không lấy thay đổi đã ghi sau đó làm nguồn patch.
            source = self._baseline_text()
        else:
            source = {}
            for name, data in self._snapshot().items():
                try:
                    # Tạo bản văn bản của snapshot hiện tại, bỏ file không decode được.
                    source[name] = data.decode("utf-8-sig").replace("\r\n", "\n")
                except UnicodeError:
                    continue
        # Parser tính toàn bộ kết quả trong RAM: {tên: nội_dung_mới_hoặc_None}.
        # None nghĩa là xóa; chuỗi rỗng nghĩa là giữ/tạo một file rỗng.
        changes = apply_repo_patch(source, patch)
        # Vòng 1 kiểm tra toàn bộ đường dẫn/quyền/kích thước trước khi ghi file đầu tiên.
        for name, content in changes.items():
            self._path(name)
            # Đây là patch Coder; lớp tool orchestrator quản lý role nào được gọi tool.
            self._can_write("Coder", name)
            if content is not None and len(content.encode("utf-8")) > self.max_file_bytes:
                raise ValueError("File exceeds size limit")
        # Vòng 2 mới thực hiện ghi. Chưa có rollback nếu lỗi ổ đĩa giữa nhiều lần ghi.
        for name, content in changes.items():
            if content is None:
                # Patch xóa file: chỉ gọi delete_file nếu file hiện tại còn tồn tại.
                if self._path(name).exists():
                    self.delete_file(role="Coder", path=name)
            else:
                # Patch thêm/sửa file dùng lại edit_file để kiểm tra và ghi nhận thay đổi.
                self.edit_file(role="Coder", path=name, content=content)
        # Trả các đường dẫn trong patch theo thứ tự ổn định.
        return {"paths": sorted(changes)}

    # Agent dùng tool này để chạy code, build hoặc kiểm tra runtime trên laptop.
    # cwd được kiểm tra trong repo; đây không phải cơ chế cách ly tiến trình.
    async def run_command(self, *, command, cwd=".", env=None, timeout_seconds=None, unlimited=False):
        directory = self._path(cwd, allow_root=True)
        if not directory.is_dir():
            raise ValueError("Command cwd is not a repository directory")
        return await self.executor.run_command(self.root, command=command, cwd=cwd,
                                               env=env, timeout_seconds=timeout_seconds, **({"unlimited": True} if unlimited else {}))

    # async/await cho phép chờ tiến trình local mà không chặn toàn bộ event loop.
    # command là argv, cwd tính từ repo, env bổ sung cho môi trường tiến trình local;
    # report_path là báo cáo test mà runner đọc. require_generated là tham số cũ,
    # còn giữ cho tương thích nhưng hiện không được dùng trong thân hàm.
    async def run_tests(self, *, command=None, cwd=None, env=None, report_path=None, require_generated=True, unlimited=False):
        # Lấy cả source và test hiện tại, kể cả test có sẵn trong repo.
        files = self._snapshot()
        if command is not None:
            # Có command mới thì thay toàn bộ lựa chọn; thiếu cwd/env dùng '.' và {}.
            # Chỉ truyền cwd/env/report_path mà không truyền command sẽ không cập nhật spec.
            self.test_spec = {"command": command, "cwd": cwd or ".", "env": env or {}, "report_path": report_path}
        if self.test_spec is None:
            # Fallback chỉ khi thấy đường dẫn test Python theo quy ước tên.
            # Đây là dấu hiệu để thử unittest, không chứng minh framework thật là unittest.
            if any(test_path(name) and name.endswith(".py") for name in files):
                # Ưu tiên tìm test ở tests/, rồi test/, nếu không có thì ở gốc repo.
                start = "tests" if (self.root / "tests").is_dir() else "test" if (self.root / "test").is_dir() else "."
                # Lưu fallback để lần gọi không tham số tiếp theo lặp lại cùng lệnh.
                self.test_spec = {"command": ["python", "-m", "unittest", "discover", "-s", start], "cwd": ".", "env": {}}
            else:
                # Chưa biết cách test thì trả lỗi quan sát, yêu cầu agent tự chọn command.
                # Nhánh này chưa chạy tiến trình và không báo PASS khi chưa chạy test.
                return {"status": "error", "tests_run": 0, "failed": 0, "test_cases": [],
                        "summary": "Inspect repo and select run_tests(command=[...], cwd=...). No test command was selected."}
        # Lấy lệnh đã chọn ở lần này hoặc lần gọi trước trong cùng run.
        spec = self.test_spec
        # Kiểm tra cwd thuộc repo, cho phép '.' biểu thị gốc.
        directory = self._path(spec["cwd"], allow_root=True)
        if not directory.is_dir():
            raise ValueError("Test cwd is not a repository directory")
        # **spec bung dict thành keyword arguments; executor chạy test trực tiếp
        # trên repo thật và trả report, không đưa code qua container.
        return await self.executor.run_tests(self.root, **spec, **({"unlimited": True} if unlimited else {}))
    


