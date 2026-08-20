"""Run a Mac mini QA release upgrade only after immutable images are cached."""

from scripts.operations.deploy_macmini_qa import main_upgrade

if __name__ == "__main__":
    raise SystemExit(main_upgrade())
