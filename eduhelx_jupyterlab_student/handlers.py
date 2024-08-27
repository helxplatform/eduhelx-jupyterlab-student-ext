import json
import os
import backoff
import requests
import subprocess
import tempfile
import shutil
import tornado
import time
import asyncio
import traceback
from urllib.parse import urlparse
from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.base.websocket import WebSocketMixin as WSMixin
from jupyter_server.auth.decorator import ws_authenticated
from tornado.websocket import WebSocketHandler as WSHandler
from jupyter_server.utils import url_path_join
from pathlib import Path
from datetime import datetime
from collections.abc import Iterable
from .config import ExtensionConfig
from eduhelx_utils.git import (
    InvalidGitRepositoryException,
    clone_repository, fetch_repository, init_repository,
    get_tail_commit_id, get_repo_name, add_remote,
    stage_files, commit, push, get_commit_info,
    get_modified_paths, get_repo_root as get_git_repo_root,
    checkout, reset as git_reset, get_head_commit_id, merge as git_merge,
    abort_merge, delete_local_branch, is_ancestor_commit,
    stash_changes, pop_stash, diff_status as git_diff_status,
    restore as git_restore, rm as git_rm
)
from eduhelx_utils.api import Api, AuthType, APIException
from eduhelx_utils.process import execute
from .student_repo import StudentClassRepo, NotStudentClassRepositoryException
from ._version import __version__

class AppContext:
    def __init__(self, serverapp):
        self.serverapp = serverapp
        self.config = ExtensionConfig(self.serverapp)
        api_config = dict(
            api_url=self.config.GRADER_API_URL,
            user_onyen=self.config.USER_NAME,
            jwt_refresh_leeway_seconds=self.config.JWT_REFRESH_LEEWAY_SECONDS
        )
        # If autogen password happens to be set (e.g. if running locally), then use it for convenience.
        if self.config.USER_AUTOGEN_PASSWORD != "":
            self.api = Api(
                **api_config,
                user_autogen_password=self.config.USER_AUTOGEN_PASSWORD,
                auth_type=AuthType.PASSWORD
            )
        else:
            self.api = Api(
                **api_config,
                appstore_access_token=self.config.ACCESS_TOKEN,
                auth_type=AuthType.APPSTORE_STUDENT
            )

    async def get_repo_root(self):
        course = await self.api.get_course()
        return StudentClassRepo._compute_repo_root(course["name"])
        

class BaseHandler(APIHandler):
    context: AppContext = None

    @property
    def config(self) -> ExtensionConfig:
        return self.context.config

    @property
    def api(self) -> Api:
        return self.context.api

    # Default error handling
    def write_error(self, status_code, **kwargs):
        # If exc_info is present, the error is unhandled.
        if "exc_info" not in kwargs: return

        cls, exc, traceback = kwargs["exc_info"]
        if isinstance(exc, APIException):
            self.set_status(status_code)
            self.finish(exc.response.text)
    
    # Default error handling
    def write_error(self, status_code, **kwargs):
        # If exc_info is present, the error is unhandled.
        if "exc_info" not in kwargs: return

        cls, exc, traceback = kwargs["exc_info"]
        if isinstance(exc, APIException):
            self.set_status(status_code)
            self.finish(exc.response.text)
    
class WebsocketHandler(WSMixin, WSHandler, BaseHandler):
    clients = []
    queued_messages = []

    def check_origin(self, origin):
        return True

    def set_default_headers(self):
        pass

    def get_compression_options(self):
        return self.settings.get("websocket_compression_options", None)
    
    def prepare(self, *args, **kwargs):
        try:
            del kwargs["_redirect_to_login"]
        except: pass
        return JupyterHandler.prepare(self, *args, **kwargs)

    @ws_authenticated
    async def open(self):
        if self not in self.clients: self.clients.append(self)
        while len(self.queued_messages) > 0:
            message = self.queued_messages.pop()
            self.emit(*message[0], **message[1])

    def on_message(self, message):
        print("message", message)

    def on_close(self):
        if self in self.clients: self.clients.remove(self)

    @classmethod
    def emit(cls, *args, **kwargs):
        if len(cls.clients) == 0:
            cls.queued_messages.append((args, kwargs))
        for client in cls.clients:
            client.write_message(*args, **kwargs)

class CourseAndStudentHandler(BaseHandler):
    async def get_value(self):
        student = await self.api.get_my_user()
        course = await self.api.get_course()
        return json.dumps({
            "student": student,
            "course": course
        })
    
    @tornado.web.authenticated
    async def get(self):
        self.finish(await self.get_value())

class AssignmentsHandler(BaseHandler):
    async def get_value(self, current_path: str):
        current_path_abs = os.path.realpath(current_path)

        student = await self.api.get_my_user()
        assignments = await self.api.get_my_assignments()
        course = await self.api.get_course()

        value = {
            "current_assignment": None,
            "assignments": None,
        }

        try:
            student_repo = StudentClassRepo(course, assignments, current_path_abs)
        except Exception:
            return json.dumps(value)

        # Add absolute path to assignment so that the frontend
        # extension knows how to open the assignment without having
        # to know the repository root.
        for assignment in assignments:
            # The frontend can only access files under directory where the Jupyter server is running,
            # so we need to make sure the "absolute" path is actually relative to the Jupyter server
            cwd = os.getcwd()
            rel_assignment_path = os.path.relpath(
                student_repo.get_assignment_path(assignment),
                cwd
            )
            # The cwd is the root in the frontend, so treat the path as such.
            # NOTE: IMPORTANT: this field is NOT absolute on the server. It's only the absolute path for the webapp.
            assignment["absolute_directory_path"] = os.path.join("/", rel_assignment_path)
        value["assignments"] = assignments

        current_assignment = student_repo.current_assignment
        # The student is in their repo, but we still need to check if they're actually in an assignment directory.
        if current_assignment is None:
            # If user is not in an assignment, we're done. Just leave current_assignment as None.
            return json.dumps(value)
        
        submissions = await self.api.get_my_submissions(current_assignment["id"])
        for submission in submissions:
            submission["commit"] = get_commit_info(submission["commit_id"], path=student_repo.repo_root)
        current_assignment["submissions"] = submissions
        current_assignment["staged_changes"] = []
        for modified_path in get_modified_paths(path=student_repo.repo_root):
            full_modified_path = Path(student_repo.repo_root) / modified_path["path"]
            abs_assn_path = Path(student_repo.repo_root) / current_assignment["directory_path"]
            try:
                path_relative_to_assn = full_modified_path.relative_to(abs_assn_path)
                modified_path["path_from_repo"] = modified_path["path"]
                modified_path["path_from_assn"] = str(path_relative_to_assn)
                current_assignment["staged_changes"].append(modified_path)
            except ValueError:
                # This path is not part of the current assignment directory
                pass
        
        value["current_assignment"] = current_assignment
        return json.dumps(value)

    @tornado.web.authenticated
    async def get(self):
        current_path: str = self.get_argument("path")
        self.finish(await self.get_value(current_path))
            

class SubmissionHandler(BaseHandler):
    @tornado.web.authenticated
    async def post(self):
        data = json.loads(self.request.body)
        submission_summary: str = data["summary"]
        submission_description: str | None = data.get("description")
        current_path: str = data["current_path"]
        current_path_abs = os.path.realpath(current_path)

        student = await self.api.get_my_user()
        assignments = await self.api.get_my_assignments()
        course = await self.api.get_course()

        try:
            student_repo = StudentClassRepo(course, assignments, current_path_abs)
        except InvalidGitRepositoryException:
            self.set_status(400)
            self.finish(json.dumps({
                "message": "Not in a git repository"
            }))
            return
        except NotStudentClassRepositoryException:
            self.set_status(400)
            self.finish(json.dumps({
                "message": "Not in student's class repository"
            }))
            return
        
        if student_repo.current_assignment is None:
            self.set_status(400)
            self.finish(json.dumps({
                "message": "Not in an assignment directory"
            }))
            return

        student_notebook_path = student_repo.current_assignment_path / student_repo.current_assignment["student_notebook_path"]
        with open(student_notebook_path, "r") as f:
            student_notebook_content = f.read()
        
        rollback_id = get_head_commit_id(path=student_repo.repo_root)
        stage_files(".", path=student_repo.current_assignment_path)
        
        try:
            commit_id = commit(
                submission_summary,
                submission_description if submission_description else None,
                path=student_repo.current_assignment_path
            )
        except Exception as e:
            # If the commit fails then unstage the assignment files.
            git_reset(".", path=student_repo.current_assignment_path)
            self.set_status(500)
            self.finish(str(e))
            return

        try:
            await self.api.create_submission(
                student_repo.current_assignment["id"],
                commit_id,
                student_notebook_content
            )
        except Exception as e:
            # If the submission fails create in the API, rollback the local commit to the previous head.
            git_reset(rollback_id, path=student_repo.repo_root)
            self.set_status(e.response.status_code)
            self.finish(e.response.text)
            return
        
        # We need to create the submission in the API before we push the changes to the remote,
        # so that we don't push the stages changes without actually creating a submission for the user
        # (which would be very misleading)
        try:
            push(StudentClassRepo.ORIGIN_REMOTE_NAME, StudentClassRepo.MAIN_BRANCH_NAME, path=student_repo.current_assignment_path)
            self.finish()
        except Exception as e:
            # Need to rollback the commit if push failed too.
            git_reset(rollback_id, path=student_repo.repo_root)
            self.set_status(500)
            self.finish(str(e))

class NotebookFilesHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        course = await self.api.get_course()
        assignments = await self.api.get_my_assignments()

        assignment_notebooks = {}
        for assignment in assignments:
            repo_root = StudentClassRepo._compute_repo_root(course["name"]).resolve()
            assignment_path = repo_root / assignment["directory_path"]

            notebooks = [path.relative_to(assignment_path) for path in assignment_path.rglob("*.ipynb")]
            notebooks = [path for path in notebooks if ".ipynb_checkpoints" not in path.parts]
            # Sort by nestedness, then alphabetically
            notebooks.sort(key=lambda path: (len(path.parents), str(path)))

            assignment_notebooks[assignment["id"]] = [str(path) for path in notebooks]

        self.finish(json.dumps({
            "notebooks": assignment_notebooks
        }))

class SettingsHandler(BaseHandler):
    @tornado.web.authenticated
    async def get(self):
        server_version = str(__version__)
        repo_root = await self.context.get_repo_root()

        self.finish(json.dumps({
            "serverVersion": server_version,
            "repoRoot": str(repo_root)
        }))


async def create_repo_root_if_not_exists(context: AppContext, course) -> None:
    repo_root = StudentClassRepo._compute_repo_root(course["name"])
    if not repo_root.exists():
        repo_root.mkdir(parents=True)

async def create_ssh_config_if_not_exists(context: AppContext, course, student) -> None:
    settings = await context.api.get_settings()
    repo_root = StudentClassRepo._compute_repo_root(course["name"]).resolve()
    ssh_config_dir = repo_root / ".ssh"
    ssh_config_file = ssh_config_dir / "config"
    ssh_identity_file = ssh_config_dir / "id_gitea"
    ssh_public_key_file = ssh_config_dir / "id_gitea.pub"

    ssh_public_url = student["fork_remote_url"]
    if not urlparse(ssh_public_url).scheme:
        ssh_public_url = "ssh://" + ssh_public_url

    ssh_private_url = settings["gitea_ssh_url"] if not context.config.LOCAL else "ssh://git@localhost:2222"
    if not urlparse(ssh_private_url).scheme:
        ssh_private_url = "ssh://" + ssh_private_url 

    ssh_public_url_parsed = urlparse(ssh_public_url)
    ssh_private_url_parsed = urlparse(ssh_private_url)

    ssh_public_hostname = ssh_public_url_parsed.hostname
    ssh_private_hostname = ssh_private_url_parsed.hostname
    ssh_port = ssh_private_url_parsed.port or 2222
    ssh_user = ssh_private_url_parsed.username or "git"
    
    if not ssh_identity_file.exists():
        ssh_config_dir.mkdir(parents=True, exist_ok=True)
        execute(["chmod", "700", ssh_config_dir])
        execute(["ssh-keygen", "-t", "rsa", "-f", ssh_identity_file, "-N", ""])
        execute(["chmod", "444", ssh_public_key_file])
        execute(["chmod", "600", ssh_identity_file])
    with open(ssh_config_file, "w+") as f:
        # Host (public Gitea URL) is rewritten as an alias to HostName (private ssh URL)
        f.write( 
            # Note that Host is really a hostname in SSH config. and is an alias to HostName here.
            f"Host { ssh_public_hostname }\n" \
            f"   User { ssh_user }\n" \
            f"   Port { ssh_port }\n" \
            f"   IdentityFile { ssh_identity_file }\n" \
            f"   HostName { ssh_private_hostname }\n" \
            f"   StrictHostKeyChecking no\n" \
            f"   UserKnownHostsFile /dev/null\n"
        )
    with open(ssh_public_key_file, "r") as f:
        public_key = f.read()
        await context.api.set_ssh_key("jls-client", public_key)

async def clone_repo_if_not_exists(context: AppContext, course, student) -> None:
    repo_root = StudentClassRepo._compute_repo_root(course["name"])
    try:
        # We're just confirming that the repo root is a git repository.
        # If it is, don't need to clone
        get_git_repo_root(path=repo_root)
    except InvalidGitRepositoryException:
        # We're not going to bother checking if fork_cloned is False actually.
        # If the repo isn't properly setup for any reason, we'll just rename
        # whatever is using that directory name, then try to set it up again.
        # If we fail at any point, just abort so the extension crashes.
        
        master_repository_url = course["master_remote_url"]
        student_repository_url = student["fork_remote_url"]

        def is_repo_populated():
            if not repo_root.exists(): return False
            # Literally, are there any files in the repo that aren't dot-files or in dot-directories?
            # There will always be some files in the repo root, even if just created, such as git and ssh config
            return any([
                path for path in repo_root.rglob("*") if not any([
                    part.startswith(".") for part in path.parts
                ])
            ])
        # We absolutely don't want to allow overriding any existing files.
        # To be extra careful, we will move the existing directory if it isn't empty.
        if is_repo_populated():
            c = 0
            uniq_rname = str(repo_root) + "~{}"
            while os.path.exists(uniq_rname.format(c)):
                c += 1
            repo_root.rename(uniq_rname.format(c))
            # Recreate the directory
            repo_root.mkdir()
            print(123498102401892384, uniq_rname.format(c))
            await set_git_authentication(context, course, student)

        """ Now we're working with a new, empty directory. """
        try:
            # This block should never fail. If it does, just abort.
            init_repository(repo_root)
            await set_git_authentication(context, course, student)
            add_remote(StudentClassRepo.UPSTREAM_REMOTE_NAME, master_repository_url, path=repo_root)
            add_remote(StudentClassRepo.ORIGIN_REMOTE_NAME, student_repository_url, path=repo_root)

            @backoff.on_exception(backoff.constant, Exception, interval=2.5, max_time=15)
            def try_fetch(remote):
                fetch_repository(remote, path=repo_root)

            # If either of these reach backoff and fail, just abort
            try_fetch(StudentClassRepo.ORIGIN_REMOTE_NAME)
            checkout(f"{ StudentClassRepo.MAIN_BRANCH_NAME }", path=repo_root)
            try_fetch(StudentClassRepo.UPSTREAM_REMOTE_NAME)

            @backoff.on_exception(backoff.constant, Exception, interval=2.5, max_time=15)
            async def mark_as_cloned():
                await context.api.mark_my_fork_as_cloned()

            # Mark the fork as cloned. If this fails, just abort.
            await mark_as_cloned()
        except Exception as e:
            # At this point, we're removing a folder we just made, so no chance of deleting user data.
            shutil.rmtree(repo_root)
            raise e

async def set_git_authentication(context: AppContext, course, student) -> None:
    repo_root = StudentClassRepo._compute_repo_root(course["name"]).resolve()
    student_repository_url = student["fork_remote_url"]
    ssh_config_file = repo_root / ".ssh" / "config"
    ssh_identity_file = repo_root / ".ssh" / "id_gitea"
    use_password_auth = urlparse(student_repository_url).scheme in ["http", "https"]
    
    try:
        get_git_repo_root(path=repo_root)
        execute(["git", "config", "--local", "--unset-all", "credential.helper"], cwd=repo_root)
        execute(["git", "config", "--local", "--unset-all", "core.sshCommand"], cwd=repo_root)

        execute(["git", "config", "--local", "user.name", context.config.USER_NAME], cwd=repo_root)
        execute(["git", "config", "--local", "user.email", student["email"]], cwd=repo_root)
        execute(["git", "config", "--local", "author.name", context.config.USER_NAME], cwd=repo_root)
        execute(["git", "config", "--local", "author.email", student["email"]], cwd=repo_root)
        execute(["git", "config", "--local", "committer.name", context.config.USER_NAME], cwd=repo_root)
        execute(["git", "config", "--local", "committer.email", student["email"]], cwd=repo_root)

        if use_password_auth:
            execute(["git", "config", "--local", "credential.helper", ""], cwd=repo_root)
            execute(["git", "config", "--local", "--add", "credential.helper", context.config.CREDENTIAL_HELPER], cwd=repo_root)
        else:
            execute(["git", "config", "--local", "core.sshCommand", f'ssh -F { ssh_config_file } -i { ssh_identity_file }'], cwd=repo_root)
    except InvalidGitRepositoryException:
        config_path = repo_root / ".git" / "config"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w+") as f:
            ssh_credential_config = (
                f"    sshCommand = ssh -F { ssh_config_file } -i { ssh_identity_file }\n"
            ) if not use_password_auth else ""
            password_credential_config = (
                f"    helper = ''\n"
                f"    helper = { context.config.CREDENTIAL_HELPER }\n"
            ) if use_password_auth else ""
            credential_config = (
                "[core]\n"
                f"{ ssh_credential_config }"
                "[user]\n"
                f"    name = { context.config.USER_NAME }\n"
                f"    email = { student['email'] }\n"
                "[author]\n"
                f"    name = { context.config.USER_NAME }\n"
                f"    email = { student['email'] }\n"
                "[committer]\n"
                f"    name = { context.config.USER_NAME }\n"
                f"    email = { student['email'] }\n"
                f"[credential]\n"
                f"{ password_credential_config }"
            )
            f.write(credential_config)

    if use_password_auth:
        parsed = urlparse(student_repository_url)
        protocol, host = parsed.scheme, parsed.netloc
        credentials = \
            f"protocol={ protocol }\n" \
            f"host={ host }\n" \
            f"username={ context.config.USER_NAME }\n" \
            f"password={ context.config.USER_AUTOGEN_PASSWORD }"
        execute(["git", "credential", "approve"], stdin_input=credentials, cwd=repo_root)

async def set_root_folder_permissions(context: AppContext) -> None:
    # repo_root = await context.get_repo_root()
    # execute(["chown", "root", repo_root.parent])
    # execute(["chmod", "+t", repo_root.parent])
    # execute(["chmod", "a-w", repo_root.parent])
    ...

async def sync_upstream_repository(context: AppContext, course) -> None:
    assignments = await context.api.get_my_assignments()
    repo_root = StudentClassRepo._compute_repo_root(course["name"])

    def backup_file(conflict_path: Path):
        print("BACKING UP FILE", conflict_path)
        full_conflict_path = repo_root / conflict_path
        if full_conflict_path in file_contents:
            # Backup the student's changes to a new file.
            backup_path = repo_root / Path(f"{ conflict_path }~{ isonow }~backup")
            with open(backup_path, "wb+") as f:
                f.write(file_contents[full_conflict_path])
        else:
            print(str(conflict_path), "deleted locally, cannot create a backup.")

    def move_untracked_files():
        # In case there are no files, we still want to make the dir so no error when deleting later.
        untracked_files_dir.mkdir(parents=True, exist_ok=True)
        for file in untracked_files:
            untracked_path = untracked_files_dir / file
            untracked_path.parent.mkdir(parents=True, exist_ok=True)
            (repo_root / file).rename(untracked_path)
    
    def restore_untracked_files():
        # Git refuses to allow you to apply a stash if any untracked changes within the stash exist locally.
        # Thus, we have to manually move and then backup untracked files after merging.
        for original_file in untracked_files:
            full_original_file_path = repo_root / original_file
            untracked_path = untracked_files_dir / original_file

            if not full_original_file_path.exists():
                # If the file doesn't exist post-merge, it hasn't been changed at all, and we can just
                # move the file back to its original path in the repo.
                untracked_path.rename(full_original_file_path)
            elif full_original_file_path.read_bytes() != untracked_path.read_bytes():
                # If the file exists post merge, but its content is the exact same, we woudn't need to take any actions.
                # The file exists but its content has changed, so backup the old version.
                print(f"Couldn't restore untracked file '{ original_file }' as it already exists on HEAD, backing up instead...")
                backup_file(original_file)

    # Grab every overwritable path inside the repository.
    # Note: we need to this multiple times, since we can only pick up paths that exist on disk.
    # If the local head deleted a file, it won't be picked up in the first pass.
    # Vice-versa, if the merge head deleted a file, it won't be picked up in the second pass.
    def gather_overwritable_paths():
        for assignment in assignments:
            for glob_pattern in assignment["overwritable_files"]:
                overwritable_paths.update((repo_root / assignment["directory_path"]).glob(glob_pattern))

    def rename_merge_conflicts(merge_conflicts, source):
        conflict_types = {
            conflict["path"] : conflict["modification_type"] for conflict in get_modified_paths(path=repo_root)
            if conflict["path"] in merge_conflicts
        }
        for conflict in merge_conflicts:
            if repo_root / conflict not in overwritable_paths:
                # If the file isn't overwritable, make a backup of it (as long as it's not deleted locally).
                print("Encountered non-overwriteable merge conflict", conflict, ". Creating backup...")
                backup_file(conflict)
            else:
                print(f"Detected overwritable merge conflict: '{ conflict }'")
            
            # Overwrite the file with its incoming version -- resolve the conflict.
            if conflict_types[conflict][1] != "D":
                git_restore(conflict, source=source, staged=True, worktree=True, path=repo_root)
            else:
                # If the conflict was deleted on the merge head, git restore won't be able to restore it.
                # Instead, just update the index/worktree to also delete the file.
                git_rm(conflict, cached=False, path=repo_root)


    try:
        fetch_repository(StudentClassRepo.UPSTREAM_REMOTE_NAME, path=repo_root)
        # In case we've pushed directly to the student's repository on the remote for some reason (through Gitea-Assist)
        fetch_repository(StudentClassRepo.ORIGIN_REMOTE_NAME, path=repo_root)
    except:
        print("Fatal: Couldn't fetch remote tracking branches, aborting sync...")
        return

    checkout(StudentClassRepo.MAIN_BRANCH_NAME, path=repo_root)
    local_head = get_head_commit_id(path=repo_root)
    upstream_head = get_head_commit_id(StudentClassRepo.UPSTREAM_TRACKING_BRANCH, path=repo_root)
    merge_branch_name = StudentClassRepo.MERGE_STAGING_BRANCH_NAME.format(local_head[:8], upstream_head[:8])
    if is_ancestor_commit(descendant=local_head, ancestor=upstream_head, path=repo_root):
        # If the local head is a descendant of the local head,
        # then any upstream changes have already been merged in.
        print(f"Upstream and local heads are merged, nothing to sync...")
        return
    
    # Make certain the merge branch is empty before we start.
    try: delete_local_branch(merge_branch_name, force=True, path=repo_root)
    except: pass
    # Branch onto the merge branch off the user's head
    checkout(merge_branch_name, new_branch=True, path=repo_root)


    isonow = datetime.now().isoformat()
    # These are relative to the repo root.
    file_contents = { path: path.read_bytes() for path in repo_root.rglob("*") if path.is_file() and ".git" not in path.parts }
    untracked_files = {
        f["path"] for f in get_modified_paths(untracked=True, path=repo_root)
        if f["modification_type"] == "??"
    }
    untracked_files_dir = repo_root / f".untracked-{ isonow }"
    overwritable_paths = set()

    gather_overwritable_paths()

    # Merge the upstream tracking branch into the temp merge branch
    try:
        print(f"Merging { StudentClassRepo.UPSTREAM_TRACKING_BRANCH } ({ upstream_head[:8] }) --> { StudentClassRepo.MAIN_BRANCH_NAME } ({ local_head[:8] }) on branch { merge_branch_name }")
        
        # We move untracked files because git can't merge them, so it will refuse if a conflict
        # would be caused, which we don't want. 
        move_untracked_files()

        # We have to stash because git refuses to merge if the merge would overwrite local changes.
        # NOTE: we don't use git stash --include-untracked because it does not work properly with merge.
        stash_changes(path=repo_root)

        # Merge the upstream tracking branch into the merge branch
        merge_conflicts = git_merge(StudentClassRepo.UPSTREAM_TRACKING_BRANCH, commit=False, path=repo_root)
        gather_overwritable_paths() # pick up paths introduced by the merge head
        rename_merge_conflicts(merge_conflicts, source="MERGE_HEAD") # restore conflicts using their incoming version from the MERGE_HEAD

        commit(None, no_edit=True, path=repo_root)

        # After popping, we could have further conflicts between the student's stashed changes and the new local head.
        pop_stash(path=repo_root)
        stash_conflicts = git_diff_status(diff_filter="U", path=repo_root)
        gather_overwritable_paths() # technically, not really necessary since we gather before stashing.
        rename_merge_conflicts(stash_conflicts, source="HEAD")

    except Exception as e:
        # Cleanup the merge branch and return to main
        print("Fatal: Can't merge upstream changes into student repository", e)
        # Since we force checkout and then delete the temp merge branch, it doesn't particularly
        # matter to us if the merge fails to abort, since we delete the MERGE_HEAD regardless.
        try: abort_merge(path=repo_root)
        except:
            print("(failed to abort merge)")
        # if an error occurs after we've already popped, there won't be anything to pop on the stack.
        try: pop_stash(path=repo_root)
        except:
            print("(failed to pop stash, already popped)")
        checkout(StudentClassRepo.MAIN_BRANCH_NAME, force=True, path=repo_root)
        delete_local_branch(merge_branch_name, force=True, path=repo_root)
        return
    
    finally:
        # It doesn't really matter when we restore these, as long as it happens post-merge.
        restore_untracked_files()
        shutil.rmtree(untracked_files_dir)

    checkout(StudentClassRepo.MAIN_BRANCH_NAME, path=repo_root)

    # If we successfully merged it, we can go ahead and merge the temp branch into our actual branch
    try:
        print(f"Merging { merge_branch_name } --> { StudentClassRepo.MAIN_BRANCH_NAME }")
        # Merge the merge staging branch into the actual branch, don't need to commit since fast forward
        # We don't need to check for conflicts here since the actual branch can now be fast forwarded.
        git_merge(merge_branch_name, ff_only=True, commit=False, path=repo_root)

    except Exception as e:
        # Merging from temp to actual branch failed.
        print(f"Fatal: Failed to merge the merge staging branch into actual branch", e)
        # Try to abort the merge, if started and unconcluded.
        try: abort_merge(path=repo_root)
        except: print("(failed to abort)")
    
    finally:
        delete_local_branch(merge_branch_name, force=True, path=repo_root)
        
    # TODO: when websockets added, ping the client if anything was changed.
    added_files = git_diff_status(f"{local_head}..{upstream_head}", diff_filter="A", path=repo_root)
    WebsocketHandler.emit({
        "type": "downsync",
        "files": added_files
    })

async def setup_backend(context: AppContext):
    try:
        course = await context.api.get_course()
        student = await context.api.get_my_user()
        await create_repo_root_if_not_exists(context, course)
        await create_ssh_config_if_not_exists(context, course, student)
        await set_git_authentication(context, course, student)
        await clone_repo_if_not_exists(context, course, student)
        await set_root_folder_permissions(context)
        while True:
            print("Pulling in upstream changes...")
            await sync_upstream_repository(context, course)
            print(f"Sleeping for { context.config.UPSTREAM_SYNC_INTERVAL }...")
            await asyncio.sleep(context.config.UPSTREAM_SYNC_INTERVAL)
    except:
        print(traceback.format_exc())

def setup_handlers(server_app):
    web_app = server_app.web_app
    BaseHandler.context = AppContext(server_app)
    
    loop = asyncio.get_event_loop()
    asyncio.run_coroutine_threadsafe(setup_backend(BaseHandler.context), loop)

    host_pattern = ".*$"

    base_url = web_app.settings["base_url"]
    handlers = [
        ("ws", WebsocketHandler),
        ("assignments", AssignmentsHandler),
        ("course_student", CourseAndStudentHandler),
        ("submit_assignment", SubmissionHandler),
        ("notebook_files", NotebookFilesHandler),
        ("settings", SettingsHandler)
    ]

    handlers_with_path = [
        (
            url_path_join(base_url, "eduhelx-jupyterlab-student", *(uri if not isinstance(uri, str) else [uri])),
            handler
        ) for (uri, handler) in handlers
    ]
    web_app.add_handlers(host_pattern, handlers_with_path)
