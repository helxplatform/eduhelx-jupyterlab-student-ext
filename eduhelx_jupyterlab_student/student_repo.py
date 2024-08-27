import os
from pathlib import Path
from eduhelx_utils.git import InvalidGitRepositoryException
from eduhelx_utils import git

class NotStudentClassRepositoryException(Exception):
    pass

class NotInAnAssignmentException(Exception):
    pass


""" Note: this class is naive to the fixed repo path. It is designed for
relative interaction with class repository filepaths WHILE inside the repository. """
class StudentClassRepo:
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

    def __init__(self, course, assignments, current_path):
        self.course = course
        self.assignments = assignments
        self.current_path = os.path.realpath(current_path)
        
        self.repo_root = self._compute_repo_root(self.course["name"], self.current_path)
        self.current_assignment = self._compute_current_assignment(self.assignments, self.repo_root, self.current_path)

    @property
    def current_assignment_path(self) -> Path | None:
        if self.current_assignment is None: return None
        return self.get_assignment_path(self.current_assignment)
    
    def get_assignment_path(self, assignment):
        return self.repo_root / assignment["directory_path"]

    def get_protected_file_paths(self, assignment) -> list[Path]:
        files = []
        for glob_pattern in assignment["protected_files"]:
            files += self.get_assignment_path(assignment).glob(glob_pattern)
        return files
    
    @classmethod
    def _compute_repo_root(cls, course_name, current_path: str | None = None):
        """ Validates that user is in the repository root if current_path is provided """
        repo_root = Path(cls.FIXED_REPO_ROOT.format(course_name.replace(" ", "_")))
        if current_path is not None:
            try:
                Path(os.path.realpath(current_path)).relative_to(os.path.realpath(repo_root))
            except ValueError:
                raise NotStudentClassRepositoryException()
        return repo_root

    @staticmethod
    def _compute_current_assignment(assignments, repo_root, current_path):
        current_assignment = None
        current_path_abs = Path(current_path).resolve()
        for assignment in assignments:
            assignment_path = Path(os.path.join(
                repo_root,
                assignment["directory_path"]
            )).resolve()
            if assignment_path == current_path_abs or assignment_path in current_path_abs.parents:
                current_assignment = assignment
                break

        return current_assignment

    @classmethod
    def from_assignment_no_path(cls, course, assignments, assignment_id: int):
        try:
            assignment = [a for a in assignments if a["id"] == assignment_id][0]
        except IndexError:
            raise NotInAnAssignmentException
        
        repo_root = cls._compute_repo_root(course["name"]).resolve()
        assignment_path = repo_root / assignment["directory_path"]

        return cls(
            course=course,
            assignments=assignments,
            current_path=assignment_path
        )