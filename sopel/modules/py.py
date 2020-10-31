# coding=utf-8
"""py.py - Sopel Python Eval Plugin.

Copyright 2008, Sean B. Palmer, inamidst.com
Licensed under the Eiffel Forum License 2.

https://sopel.chat
"""
from __future__ import absolute_import, division, print_function, unicode_literals

from requests import get

from sopel import plugin
from sopel.config import types
from sopel.tools.web import quote


class PySection(types.StaticSection):
    """Plugin configuration definition."""

    oblique_instance = types.ValidatedAttribute(
        'oblique_instance',
        default='https://oblique.sopel.chat/')
    """The Oblique instance to use when evaluating Python expressions"""


def configure(config):
    """Interactively configure plugin.

    | name | example | purpose |
    | ---- | ------- | ------- |
    | oblique_instance | https://oblique.sopel.chat/ | The Oblique instance to use when evaluating Python expressions (see <https://github.com/sopel-irc/oblique>) |
    """
    config.define_section('py', PySection)
    config.py.configure_setting(
        'oblique_instance',
        'Enter the base URL of a custom Oblique instance (optional): '
    )


def setup(bot):
    bot.config.define_section('py', PySection)

    if not any(
        bot.config.py.oblique_instance.startswith(prot)
        for prot in ['http://', 'https://']
    ):
        raise ValueError('Oblique instance URL must start with a protocol.')

    if not bot.config.py.oblique_instance.endswith('/'):
        bot.config.py.oblique_instance += '/'


@plugin.command('py')
@plugin.output_prefix('[py] ')
@plugin.example('.py len([1,2,3])', '3', online=True, vcr=True)
def py(bot, trigger):
    """Evaluate a Python expression."""
    if not trigger.group(2):
        return bot.reply('I need an expression to evaluate.')

    query = trigger.group(2)
    uri = bot.config.py.oblique_instance + 'py/'
    answer = get(uri + quote(query)).content.decode('utf-8')
    if answer:
        bot.say(answer)
    else:
        bot.reply('Sorry, no result.')
