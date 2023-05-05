import git

class Git:
    def is_git_repo(path):
        try:
            _ = git.Repo(path).git_dir
            return True
        except git.exc.InvalidGitRepositoryError:
            return False
