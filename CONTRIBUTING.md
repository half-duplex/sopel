Submitting Issues
-----------------

When submitting issues to our
[issue tracker](https://github.com/sopel-irc/sopel/issues), it's important
that you do the following:

1. Describe your issue clearly and concisely.
2. Give Sopel the `.version` command, and include the output in your issue.
3. Note the OS you're running Sopel on, and how you installed Sopel (via your
   package manager, pip, `pip install .` straight from source)
4. Include relevant output from the log files in `~/.sopel/logs/`.

Committing Code
---------------

We prefer code to be submitted through GitHub pull requests. We do require that
code submitted to the project be licensed under the Eiffel Forum License v2,
the text of which was distributed with the source code.

In order to make it easier for us to review and merge your code, it's important
to write good commits, and good commit messages. Below are some things you
should do when you want to submit code. These aren't hard and fast rules; we
may still consider code that doesn't meet all of them. But doing the stuff
below will make our lives easier, and by extension make us more likely to
include your changes.

* Commits should focus on one thing at a time. Do include whatever you need to
  make your change work, but avoid putting unrelated changes in the same
  commit. Preferably, one change in functionality should be in exactly one
  commit—and commits should be atomic (i.e. try not to commit broken or
  intermediate states of the code).
* pep8ify your code before you commit. We don't worry about line length much
  (though it's good if you do keep lines short), but you should try to follow
  the rest of the rules.
* Test your code before you commit. We don't have a formal testing plan in
  place, but you should make sure your code works as promised before you
  commit.
* If you have cloned the repository to your personal computer, you can use our
  provided git hooks to automatically check whether your code style is up to
  Sopel's requirements and that tests still pass, before committing your code.
  The git hooks can be enabled by running `make install-hooks`, and disabled by
  running `make uninstall-hooks`,  in your root `sopel` folder.
* Make your commit messages clear and explicative. Our convention is to place
  the name of the thing you're changing in at the beginning of the message,
  followed by a colon: the plugin name for plugins, "docs" for documentation
  files, "coretasks" for `coretasks.py`, "db" for the database feature, etc.
* Python files should always have `from __future__ import annotations`
  as the first line after the module docstring.

Running from source
-------------------

Sopel is your typical Python project: you can install it from source, edit
the code using standard tooling, and take advantage of your favorite code
editor.

Assuming you are using standard tools:

* Fork Sopel's repository, and clone your fork locally.
* Create a virtualenv.
  * You can use `virtualenv` (or the `venv` built-in) directly,
    `virtualenvwrapper`, or any tool that allows you to create and manage your
    virtualenvs.
  * Your project manager may create the virtualenv for you; be sure to check
    the documentation of the tools you are using.
* Activate your virtualenv, and `cd` into your clone's folder.
* Ensure you have the latest version of `pip`, and install `wheel`.
* Install Sopel from source as an editable install, including dev dependencies,
  by running `pip install -Ue . --group dev` from the project folder.
* Run `make qa` to run linters and tests.
* Run `make cleandoc` or just `make docs` to build the documentation locally.

At this point, you are all set, at least for the Python environment; all that's
left is for you to configure your code editor the way you like, and read and
write some code and documentation.

Using branches
--------------

As previously stated, you should fork Sopel's repository to implement your
changes and commit them. Moreover, we advise you to work from your own branch.

To setup your local clone, we suggest the following steps:

```
$ cd path/to/your/dev/folder
$ git clone git@github.com:<USERNAME>/sopel.git
$ cd sopel/
$ git remote add upstream git@github.com:sopel-irc/sopel.git
$ git checkout -b <your-branch-name>
```

With that workflow, you have your local clone with a link to the upstream
repository, and a branch to work on. Once you are done with your work, you can
commit, and push to your repository.

You'll probably need, at some point, to update your repository with the new
commits from upstream. We suggest the following steps:

```
$ git checkout master
$ git fetch upstream
$ git rebase upstream/master
$ git push
```

If you never pushed on your own `master` branch, you should not need to force
the push, which is why we recommend to work on your own branch.

Said branch can be updated with rebase:

```
$ git checkout <your-branch-name>
$ git rebase master
```

To squash your commits, you can use interactive mode with
`git rebase -i master`.

In both cases, if you configured
[a GPG key](https://docs.github.com/en/authentication/managing-commit-signature-verification)
to sign your commits, don't forget the `-S` option:

* `git rebase -S master`
* `git rebase -S -i master`

Documenting Code
----------------

Hopefully you're documenting new code as you write it. We like to use these
guidelines for writing docstrings. Let's start with an example:

```
def scramble_eggs(eggs, bacon=None, spam=False):
    """Scramble eggs, with optional bacon and/or SPAM.

    :param int eggs: how many eggs to scramble
    :param bacon: optional bacon to put in the ``eggs``
    :type bacon: :class:`sopel.tools.breakfast.Bacon`
    :param bool spam: whether to put SPAM in the scrambled ``eggs``
    :return: the scrambled eggs
    :rtype: :term:`iterable` of :class:`sopel.tools.breakfast.Egg`

    You should create and customize your own :class:`~sopel.tools.bacon.Bacon`
    object to pass in. See that class's documentation to learn how to make the
    bacon extra crispy, chopped, diced, etc.

    This function will generate SPAM as needed, since it is not customizable.

    .. versionadded:: 7.1
    .. seealso::

       To make an omelet, use :func:`make_omelet`.

    """
    # <function code>
```

The basic components of the ideal Sopel function docstring are:

* A one-line description, as an imperative sentence (ending in a period)
* Function parameters, described in brief, with type annotations
* Description and type of return value
* Longer notes on function parameters and behavior, if needed
* Sphinx annotations and cross-references

Issue Tags
----------

If you're looking to hack on some code, you should consider fixing one of the
issues that has been reported to the GitHub issue tracker. Here's a quick guide
to the tags we use:

* Easyfix           - A great place for a new developer to start off, Easyfix
                      issues are ones which we think will be simple and quick
                      to address.
* Patches Welcome   - Things we don't plan on doing, but which we might be
                      interested to see someone else submit code for
* Bug               - An error or incorrect behavior
* Feature           - A new thing the bot should be able to do
* Tweak             - A minor change to something the bot already does, like
                      making something's output prettier, improving the
                      documentation on something, or addressing technical debt
* Tracking          - An ongoing process that involves lots of changes in lots
                      of places. Often also a Tweak.
* Low Priority      - Things we'll get around to doing eventually
* Medium Priority   - Things that need to be done soon
* High Priority     - Things that should've been done yesterday
* Tests             - Issues regarding the testing unit in tests/
* Windows           - Windows-specific bugs are labeled as such

Conduct
-------

Sopel is an inclusive project. The fact that it even exists today is thanks to
a diverse cast of contributors from around the world. Indeed, we would not be
here if not for the hard work and dedication of many people who, elsewhere,
might have been discriminated against simply because of who they are.

For that reason—and because it's just The Right Thing To Do™—everyone in the
Sopel community is expected to be professional and respectful at all times,
whether collaborating on code or just bantering in our IRC channel. Basically,
don't be a jerk, don't make personal attacks, and listen if someone else tells
you to back off. Listen _harder_ if more than one person does so.

Everyone involved with the Sopel project _is a person_, not a collection of
labels (unlike our issue tracker, which is the reverse). Remember that, but
don't forget to be awesome!
