# Contributing

Install the project and development dependencies, then enable the Git hook:

```console
pipenv sync --dev
pipenv run pre-commit install
```

Commits run isort, Black, and Flake8 against staged Python files. To check the
entire backend manually, run:

```console
pipenv run pre-commit run --all-files
```
