# Developing for Taky

Thanks for considering helping out with development! A few tips on getting
started, to help you work more quickly.

## Environment Setup

Taky uses [uv](https://docs.astral.sh/uv/) for dependency and environment
management. If you don't already have it, install it with:

```
$ curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from the repository root, create the virtual environment and install all
dependencies (including the development tools) with:

```
$ uv sync
```

This installs `taky` into `.venv` in editable mode -- any changes you make to
the source code will take effect the next time you run or import the code. Run
the programs and the test suite through `uv run`:

```
taky $ uv run taky
taky $ uv run takyctl --help
taky $ uv run python -m unittest discover
```

`uv` will automatically download and manage a suitable Python version
(see `.python-version`) if your system does not provide one.

## Development Addons

This step is optional, but very helpful for me! I like to run `black` on my
code to keep formating uniform and tidy. I've setup the project with
pre-commit so that this can happen automatically whenever you go to commit.

If you want to set this up, run

```
taky $ uv run pre-commit install
```

Now, whenever you commit changes, `pre-commit` will run `black` on your code.
If there are any changes, it will yell at you, and add the changes to staging.
You can check the diff to see what `black` did, add the changes, and then
commit again.

You can also run the linters manually:

```
taky $ uv run black .
taky $ uv run pylint taky
taky $ uv run pyright
```

This will save me some time, but is certainly not required! (I'm just happy
to have code coming in!)
