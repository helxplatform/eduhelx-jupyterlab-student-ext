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
from jupyter_server.base.handlers import APIHandler
from jupyter_server.utils import url_path_join
from pathlib import Path
from collections.abc import Iterable
from .config import ExtensionConfig
from eduhelx_utils.git import (
    InvalidGitRepositoryException,
    clone_repository, fetch_repository, init_repository,
    get_tail_commit_id, get_repo_name, add_remote,
    stage_files, commit, push, get_commit_info,
    get_modified_paths, get_repo_root as get_git_repo_root,
    checkout, reset as git_reset, get_head_commit_id,
    merge as git_merge, abort_merge, delete_local_branch,
    is_ancestor_commit
)
from eduhelx_utils.api import Api, AuthType
from eduhelx_utils.process import execute
from .student_repo import StudentClassRepo, NotStudentClassRepositoryException
from ._version import __version__

FIXED_REPO_ROOT = "eduhelx/{}-student" # <class_name>
ORIGIN_REMOTE_NAME = "origin"
UPSTREAM_REMOTE_NAME = "upstream"
# Local branch
MAIN_BRANCH_NAME = "main"
# We have to do a merge to sync changes. We stage the merge on a separate branch
# to proactively guard against a merge conflict.
MERGE_STAGING_BRANCH_NAME = "__temp__/merge_{}-from-{}" # Formatted with the local head and upstream head commit hashes
UPSTREAM_TRACKING_BRANCH = f"{ UPSTREAM_REMOTE_NAME }/{ MAIN_BRANCH_NAME }"
ORIGIN_TRACKING_BRANCH = f"{ ORIGIN_REMOTE_NAME }/{ MAIN_BRANCH_NAME }"

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
        return self._compute_repo_root(course["name"])

    @staticmethod
    def _compute_repo_root(course_name: str):
        # NOTE: the relative path for the server is the root path for the UI
        return Path(FIXED_REPO_ROOT.format(course_name.replace(" ", "_")))
        

class BaseHandler(APIHandler):
    context: AppContext = None

    @property
    def config(self) -> ExtensionConfig:
        return self.context.config

    @property
    def api(self) -> Api:
        return self.context.api

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

        current_assignment_path = student_repo.get_assignment_path(student_repo.current_assignment)
        
        rollback_id = get_head_commit_id(path=student_repo.repo_root)
        stage_files(".", path=current_assignment_path)
        
        try:
            commit_id = commit(
                submission_summary,
                submission_description if submission_description else None,
                path=current_assignment_path
            )
        except Exception as e:
            # If the commit fails then unstage the assignment files.
            git_reset(".", path=current_assignment_path)
            self.set_status(500)
            self.finish(str(e))
            return

        try:
            await self.api.create_submission(
                student_repo.current_assignment["id"],
                commit_id
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
            push(ORIGIN_REMOTE_NAME, MAIN_BRANCH_NAME, path=current_assignment_path)
            self.finish()
        except Exception as e:
            # Need to rollback the commit if push failed too.
            git_reset(rollback_id, path=student_repo.repo_root)
            self.set_status(500)
            self.finish(str(e))


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
    repo_root = context._compute_repo_root(course["name"])
    if not repo_root.exists():
        repo_root.mkdir(parents=True)

async def create_ssh_config_if_not_exists(context: AppContext, course, student) -> None:
    settings = await context.api.get_settings()
    repo_root = context._compute_repo_root(course["name"]).resolve()
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
        execute(["ssh-keygen", "-t", "rsa", "-f", ssh_identity_file, "-N", ""])
        with open(ssh_config_file, "w+") as f:
            # Host (public Gitea URL) is rewritten as an alias to HostName (private ssh URL)
            f.write( 
                # Note that Host is really a hostname in SSH config. and is an alias to HostName here.
                f"Host { ssh_public_hostname }\n" \
                f"   User { ssh_user }\n" \
                f"   Port { ssh_port }\n" \
                f"   IdentityFile { ssh_identity_file }\n" \
                f"   HostName { ssh_private_hostname }\n"
            )
        with open(ssh_public_key_file, "r") as f:
            public_key = f.read()
            await context.api.set_ssh_key("jls-client", public_key)

async def clone_repo_if_not_exists(context: AppContext, course, student) -> None:
    repo_root = context._compute_repo_root(course["name"])
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
            add_remote(UPSTREAM_REMOTE_NAME, master_repository_url, path=repo_root)
            add_remote(ORIGIN_REMOTE_NAME, student_repository_url, path=repo_root)

            @backoff.on_exception(backoff.constant, Exception, interval=2.5, max_time=15)
            def try_fetch(remote):
                fetch_repository(remote, path=repo_root)

            # If either of these reach backoff and fail, just abort
            try_fetch(ORIGIN_REMOTE_NAME)
            checkout(f"{ MAIN_BRANCH_NAME }", path=repo_root)
            try_fetch(UPSTREAM_REMOTE_NAME)

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
    repo_root = context._compute_repo_root(course["name"]).resolve()
    student_repository_url = student["fork_remote_url"]
    ssh_config_file = repo_root / ".ssh" / "config"
    ssh_identity_file = repo_root / ".ssh" / "id_gitea"
    use_password_auth = context.api.auth_type == AuthType.PASSWORD
    
    try:
        get_git_repo_root(path=repo_root)
        execute(["git", "config", "--local", "--unset-all", "credential.helper"], cwd=repo_root)
        execute(["git", "config", "--local", "--unset-all", "core.sshCommand"], cwd=repo_root)
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
                "[core]\n"
                f'    sshCommand = ssh -F { ssh_config_file } -i { ssh_identity_file }\n'
            )
            password_credential_config = (
                f"[credential]\n"
                f"    helper = ''\n"
                f"    helper = { context.config.CREDENTIAL_HELPER }\n"
            )
            credential_config = (
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
                f"{ password_credential_config }" if use_password_auth else ""
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
    repo_root = context._compute_repo_root(course["name"])

    try:
        fetch_repository(UPSTREAM_REMOTE_NAME, path=repo_root)
        # In case we've pushed directly to the student's repository on the remote for some reason (through Gitea-Assist)
        fetch_repository(ORIGIN_REMOTE_NAME, path=repo_root)
    except:
        print("Fatal: Couldn't fetch remote tracking branches, aborting sync...")

    checkout(MAIN_BRANCH_NAME, path=repo_root)
    local_head = get_head_commit_id(path=repo_root)
    upstream_head = get_head_commit_id(UPSTREAM_TRACKING_BRANCH, path=repo_root)
    merge_branch_name = MERGE_STAGING_BRANCH_NAME.format(local_head[:8], upstream_head[:8])
    if is_ancestor_commit(descendant=local_head, ancestor=upstream_head, path=repo_root):
        # If the local head is a descendant of the local head,
        # then any upstream changes have already been merged in.
        print(f"Upstream and local heads are the merged, nothing to sync...")
        return
    
    # Make certain the merge branch is empty before we start.
    delete_local_branch(merge_branch_name, force=True, path=repo_root)
    # Branch onto the merge branch off the user's head
    checkout(merge_branch_name, new_branch=True, path=repo_root)

    # Merge the upstream tracking branch into the temp merge branch
    try:
        print(f"Merging { UPSTREAM_TRACKING_BRANCH } ({ upstream_head[:8] }) --> { MAIN_BRANCH_NAME } ({ local_head[:8] }) on branch { merge_branch_name }")
        # Merge the upstream tracking branch into the merge branch
        conflicts = git_merge(UPSTREAM_TRACKING_BRANCH, commit=True, path=repo_root)
        if len(conflicts) > 0:
            raise Exception("Encountered merge conflicts during merge: ", ", ".join(conflicts))

    except Exception as e:
        print("Fatal: Can't merge upstream changes into student repository", e)
        # Cleanup the merge branch and return to main
        abort_merge(path=repo_root)
        checkout(MAIN_BRANCH_NAME, path=repo_root)
        delete_local_branch(merge_branch_name, force=True, path=repo_root)
        return

    checkout(MAIN_BRANCH_NAME, path=repo_root)

    # If we successfully merged it, we can go ahead and merge the temp branch into our actual branch
    try:
        print(f"Merging { merge_branch_name } --> { MAIN_BRANCH_NAME }")
        # Merge the merge staging branch into the actual branch, don't need to commit since fast forward
        # We don't need to check for conflicts here since the actual branch can now be fast forwarded.
        git_merge(merge_branch_name, ff_only=True, commit=False, path=repo_root)

    except Exception as e:
        # Merging from temp to actual branch failed.
        print(f"Fatal: Failed to merge the merge staging branch into actual branch", e)
        abort_merge(path=repo_root)
    
    finally:
        delete_local_branch(merge_branch_name, force=True, path=repo_root)
        
    # TODO: when websockets added, ping the client if anything was changed.

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
        ("assignments", AssignmentsHandler),
        ("course_student", CourseAndStudentHandler),
        ("submit_assignment", SubmissionHandler),
        ("settings", SettingsHandler)
    ]

    handlers_with_path = [
        (
            url_path_join(base_url, "eduhelx-jupyterlab-student", *(uri if not isinstance(uri, str) else [uri])),
            handler
        ) for (uri, handler) in handlers
    ]
    web_app.add_handlers(host_pattern, handlers_with_path)
