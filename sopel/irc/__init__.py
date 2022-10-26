""":mod:`sopel.irc` is the core IRC module for Sopel.

This sub-package contains everything that is related to the IRC protocol
(connection, commands, abstract client, etc.) and that can be used to implement
the Sopel bot.

In particular, it defines the interface for the IRC backend
(:class:`~sopel.irc.abstract_backends.AbstractIRCBackend`), and the
interface for the bot itself (:class:`~sopel.irc.AbstractBot`). This is all
internal code that isn't supposed to be used directly by a plugin developer,
who should worry about :class:`sopel.bot.Sopel` only.

.. important::

    When working on core IRC protocol related features, consult protocol
    documentation at https://modern.ircdocs.horse/

"""
# Copyright 2008, Sean B. Palmer, inamidst.com
# Copyright 2012, Elsie Powell, http://embolalia.com
# Copyright © 2012, Elad Alfassa <elad@fedoraproject.org>
# Copyright 2019, Florian Strzelecki <florian.strzelecki@gmail.com>
#
# Licensed under the Eiffel Forum License 2.
from __future__ import annotations

import abc
from collections import deque
from datetime import datetime
import logging
import os
import threading
import time
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Set,
    Tuple,
    TYPE_CHECKING,
)

from sopel import tools, trigger
from sopel.tools import identifiers
from .backends import AsyncioBackend
from .isupport import ISupport
from .utils import CapReq, safe

if TYPE_CHECKING:
    from sopel.config import Config
    from .abstract_backends import AbstractIRCBackend
    from .utils import MyInfo


__all__ = ['abstract_backends', 'backends', 'utils']

LOGGER = logging.getLogger(__name__)
ERR_BACKEND_NOT_INITIALIZED = 'Backend not initialized; is the bot running?'


class AbstractBot(abc.ABC):
    """Abstract definition of Sopel's interface."""
    def __init__(self, settings: Config):
        # private properties: access as read-only properties
        self._user: str = settings.core.user
        self._name: str = settings.core.name
        self._isupport = ISupport()
        self._myinfo: Optional[MyInfo] = None
        self._nick: identifiers.Identifier = self.make_identifier(
            settings.core.nick)

        self.backend: Optional[AbstractIRCBackend] = None
        """IRC Connection Backend."""
        self.connection_registered = False
        """Flag stating whether the IRC Connection is registered yet."""
        self.settings = settings
        """Bot settings."""
        self.enabled_capabilities: Set[str] = set()
        """A set containing the IRCv3 capabilities that the bot has enabled."""
        self._cap_reqs: Dict[str, List[CapReq]] = dict()
        """A dictionary of capability names to a list of requests."""

        # internal machinery
        self.sending = threading.RLock()
        self.last_error_timestamp: Optional[datetime] = None
        self.error_count = 0
        self.stack: Dict[identifiers.Identifier, Dict[str, Any]] = {}
        self.hasquit = False
        self.wantsrestart = False
        self.last_raw_line = ''  # last raw line received
        self.batches = {}

    @property
    def nick(self) -> identifiers.Identifier:
        """Sopel's current nick.

        Changing this while Sopel is running is unsupported and can result in
        undefined behavior.
        """
        return self._nick

    @property
    def user(self) -> str:
        """Sopel's user/ident."""
        return self._user

    @property
    def name(self) -> str:
        """Sopel's "real name", as used for WHOIS responses."""
        return self._name

    @property
    def config(self) -> Config:
        """The :class:`sopel.config.Config` for the current Sopel instance."""
        # TODO: Deprecate config, replaced by settings
        return self.settings

    @property
    def isupport(self) -> ISupport:
        """Features advertised by the server.

        :type: :class:`~.isupport.ISupport` instance
        """
        return self._isupport

    @property
    def myinfo(self) -> MyInfo:
        """Server/network information.

        :type: :class:`~.utils.MyInfo` instance

        .. versionadded:: 7.0
        """
        if self._myinfo is None:
            raise AttributeError('myinfo')
        return self._myinfo

    @property
    @abc.abstractmethod
    def hostmask(self) -> Optional[str]:
        """The bot's hostmask."""

    # Utility

    def make_identifier(self, name: str) -> identifiers.Identifier:
        """Instantiate an Identifier using the bot's context."""
        casemapping = {
            'ascii': identifiers.ascii_lower,
            'rfc1459': identifiers.rfc1459_lower,
            'rfc1459-strict': identifiers.rfc1459_strict_lower,
        }.get(self.isupport.get('CASEMAPPING'), identifiers.rfc1459_lower)
        chantypes = (
            self.isupport.get('CHANTYPES', identifiers.DEFAULT_CHANTYPES))

        return identifiers.Identifier(
            name,
            casemapping=casemapping,
            chantypes=chantypes,
        )

    def safe_text_length(self, recipient: str) -> int:
        """Estimate a safe text length for an IRC message.

        :return: the maximum possible length of a message to ``recipient``

        When the bot sends a message to a recipient (channel or nick), it has
        512 bytes minus the command, arguments, various separators and trailing
        CRLF for its text. However, this is not what other users will see from
        the server; the message forwarded to other clients will be sent using
        this format::

            :nick!~user@hostname PRIVMSG #channel :text

        Which takes more bytes, reducing the maximum length available for a
        single line of text. This method computes a safe length of text that
        can be sent using ``PRIVMSG`` or ``NOTICE`` by subtracting the size
        required by the server to convey the bot's message.

        .. seealso::

            This method is useful when sending a message using :meth:`say`,
            and can be used with :func:`sopel.tools.get_sendable_message`.

        """
        # Clients "SHOULD" assume messages will be truncated at 512 bytes if
        # the LINELEN ISUPPORT token is not present.
        # See https://modern.ircdocs.horse/#linelen-parameter
        max_line_length = self.isupport.get('LINELEN', 512)

        if self.hostmask is not None:
            hostmask_length = len(self.hostmask)
        else:
            # calculate maximum possible length, given current nick/username
            hostmask_length = (
                len(self.nick)  # own nick length
                + 1  # (! separator)
                + 1  # (for the optional ~ in user)
                + min(  # own ident length, capped to ISUPPORT or RFC maximum
                    len(self.user),
                    getattr(self.isupport, 'USERLEN', 9))
                + 1  # (@ separator)
                + 63  # <hostname> has a maximum length of 63 characters.
            )

        return (
            max_line_length
            - 1  # leading colon
            - hostmask_length  # calculated/maximum length of own hostmask prefix
            - 1  # space between prefix & command
            - 7  # PRIVMSG command
            - 1  # space before recipient
            - len(recipient.encode('utf-8'))  # target channel/nick (can contain Unicode)
            - 2  # space after recipient, colon before text
            - 2  # trailing CRLF
        )

    # Connection

    def get_irc_backend(
        self,
        host: str,
        port: int,
        source_address: Optional[Tuple[str, int]],
    ) -> AbstractIRCBackend:
        """Set up the IRC backend based on the bot's settings.

        :return: the initialized IRC backend object
        :rtype: an object implementing the interface of
                :class:`~sopel.irc.abstract_backends.AbstractIRCBackend`
        """
        timeout = int(self.settings.core.timeout)
        ping_interval = int(self.settings.core.timeout_ping_interval)
        return AsyncioBackend(
            self,
            # connection
            host=host,
            port=port,
            source_address=source_address,
            # timeout
            server_timeout=timeout,
            ping_interval=ping_interval,
            # ssl
            use_ssl=self.settings.core.use_ssl,
            certfile=self.settings.core.client_cert_file,
            keyfile=self.settings.core.client_cert_file,
            verify_ssl=self.settings.core.verify_ssl,
            ca_certs=self.settings.core.ca_certs,
            ssl_ciphers=self.settings.core.ssl_ciphers,
            ssl_minimum_version=self.settings.core.ssl_minimum_version,
        )

    def run(self, host: str, port: int = 6667) -> None:
        """Connect to IRC server and run the bot forever.

        :param str host: the IRC server hostname
        :param int port: the IRC server port
        """
        source_address = ((self.settings.core.bind_host, 0)
                          if self.settings.core.bind_host else None)

        self.backend = self.get_irc_backend(host, port, source_address)
        try:
            self.backend.run_forever()
        except KeyboardInterrupt:
            # raised only when the bot is not connected
            LOGGER.warning('Keyboard Interrupt')
            raise

    def on_connect(self) -> None:
        """Handle successful establishment of IRC connection."""
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        LOGGER.info('Connected, initiating setup sequence')

        # Request list of server capabilities. IRCv3 servers will respond with
        # CAP * LS (which we handle in coretasks). v2 servers will respond with
        # 421 Unknown command, which we'll ignore
        LOGGER.debug('Sending CAP request')
        self.backend.send_command('CAP', 'LS', '302')

        # authenticate account if needed
        if self.settings.core.auth_method == 'server':
            LOGGER.debug('Sending server auth')
            self.backend.send_pass(self.settings.core.auth_password)
        elif self.settings.core.server_auth_method == 'server':
            LOGGER.debug('Sending server auth')
            self.backend.send_pass(self.settings.core.server_auth_password)

        LOGGER.debug('Sending nick "%s"', self.nick)
        self.backend.send_nick(self.nick)
        LOGGER.debug('Sending user "%s" (name: "%s")', self.user, self.name)
        self.backend.send_user(self.user, '0', '*', self.name)

    def on_message(self, message: str) -> None:
        """Handle an incoming IRC message.

        :param str message: the received raw IRC message
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self.last_raw_line = message

        pretrigger = trigger.PreTrigger(
            self.nick,
            message,
            url_schemes=self.settings.core.auto_url_schemes,
            identifier_factory=self.make_identifier,
        )
        if all(
            cap not in self.enabled_capabilities
            for cap in ['account-tag', 'extended-join']
        ):
            pretrigger.tags.pop('account', None)

        # Batch handling
        if pretrigger.event == "BATCH":
            action = pretrigger.args[1][0]
            if action == "+":
                newbatch = Batch(pretrigger, parent=self.batches.get(pretrigger.tags.get("batch")))
                self.batches[newbatch.reference] = newbatch
            elif action == "-":
                if self.batches[pretrigger.args[1][0]]
                pass  # todo: handle batch
            else:
                LOGGER.error("Received malformed BATCH message: %r", message)
                return
        if "batch" in pretrigger.tags:
            if pretrigger.tags["batch"] not in self.batches:
                LOGGER.error("Received message in unknown batch: %r", message)
                return
            pass  # todo: add message to batch


        if pretrigger.event == 'PING':
            self.backend.send_pong(pretrigger.args[-1])
        elif pretrigger.event == 'ERROR':
            LOGGER.error("ERROR received from server: %s", pretrigger.args[-1])
            self.backend.on_irc_error(pretrigger)

        self.dispatch(pretrigger)

    def on_message_sent(self, raw: str) -> None:
        """Handle any message sent through the connection.

        :param str raw: raw text message sent through the connection

        When a message is sent through the IRC connection, the bot will log
        the raw message. If necessary, it will also simulate the
        `echo-message`_ feature of IRCv3.

        .. _echo-message: https://ircv3.net/irc/#echo-message
        """
        # Log raw message
        self.log_raw(raw, '>>')

        # Simulate echo-message
        no_echo = 'echo-message' not in self.enabled_capabilities
        echoed = ['PRIVMSG', 'NOTICE']
        if no_echo and any(raw.upper().startswith(cmd) for cmd in echoed):
            # Use the hostmask we think the IRC server is using for us,
            # or something reasonable if that's not available
            host = 'localhost'
            if self.settings.core.bind_host:
                host = self.settings.core.bind_host
            else:
                try:
                    host = self.hostmask or host
                except KeyError:
                    pass  # we tried, and that's good enough

            pretrigger = trigger.PreTrigger(
                self.nick,
                ":{0}!{1}@{2} {3}".format(self.nick, self.user, host, raw),
                url_schemes=self.settings.core.auto_url_schemes,
                identifier_factory=self.make_identifier,
            )
            self.dispatch(pretrigger)

    def on_error(self) -> None:
        """Handle any uncaptured error in the bot itself."""
        LOGGER.error('Fatal error in core, please review exceptions log.')

        err_log = logging.getLogger('sopel.exceptions')
        err_log.error(
            'Fatal error in core, handle_error() was called.\n'
            'Last Line:\n%s',
            self.last_raw_line,
        )
        err_log.exception('Fatal error traceback')
        err_log.error('----------------------------------------')

        if self.error_count > 10:
            # quit if too many errors
            dt_seconds: float = 0.0
            if self.last_error_timestamp is not None:
                dt = datetime.utcnow() - self.last_error_timestamp
                dt_seconds = dt.total_seconds()

            if dt_seconds < 5:
                LOGGER.error('Too many errors, can\'t continue')
                os._exit(1)
            # remove 1 error per full 5s that passed since last error
            self.error_count = int(max(0, self.error_count - dt_seconds // 5))

        self.last_error_timestamp = datetime.utcnow()
        self.error_count = self.error_count + 1

    def rebuild_nick(self) -> None:
        """Rebuild nick as a new identifier.

        This method exists to update the casemapping rules for the
        :class:`~sopel.tools.identifiers.Identifier` that represents the bot's
        nick, e.g. after ISUPPORT info is received.
        """
        self._nick = self.make_identifier(str(self._nick))

    def change_current_nick(self, new_nick: str) -> None:
        """Change the current nick without configuration modification.

        :param str new_nick: new nick to be used by the bot
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self._nick = self.make_identifier(new_nick)
        LOGGER.debug('Sending nick "%s"', self.nick)
        self.backend.send_nick(self.nick)

    def on_close(self) -> None:
        """Call shutdown methods."""
        self._shutdown()

    def _shutdown(self) -> None:
        """Handle shutdown tasks.

        Must be overridden by subclasses to do anything useful.
        """

    # Features

    @abc.abstractmethod
    def dispatch(self, pretrigger: trigger.PreTrigger):
        """Handle running the appropriate callables for an incoming message.

        :param pretrigger: Sopel PreTrigger object
        :type pretrigger: :class:`sopel.trigger.PreTrigger`

        .. important::
            This method **MUST** be implemented by concrete subclasses.
        """

    def log_raw(self, line: str, prefix: str) -> None:
        """Log raw line to the raw log.

        :param str line: the raw line
        :param str prefix: additional information to prepend to the log line

        The ``prefix`` is usually either ``>>`` for an outgoing ``line`` or
        ``<<`` for a received one.
        """
        if not self.settings.core.log_raw:
            return
        logger = logging.getLogger('sopel.raw')
        logger.info("%s\t%r", prefix, line)

    def cap_req(
        self,
        plugin_name: str,
        capability: str,
        arg: Optional[str] = None,
        failure_callback: Optional[Callable] = None,
        success_callback: Optional[Callable] = None,
    ) -> None:
        """Tell Sopel to request a capability when it starts.

        :param plugin_name: the plugin requesting the capability
        :param capability: the capability requested, optionally prefixed with
                           ``-`` or ``=``
        :param arg: arguments for the capability request
        :param failure_callback: a function that will be called if the
                                 capability request fails
        :param success_callback: a function that will be called if the
                                 capability is successfully requested

        By prefixing the capability with ``-``, it will be ensured that the
        capability is not enabled. Similarly, by prefixing the capability with
        ``=``, it will be ensured that the capability is enabled. Requiring and
        disabling is "first come, first served"; if one plugin requires a
        capability, and another prohibits it, this function will raise an
        exception in whichever plugin loads second. An exception will also be
        raised if the plugin is being loaded after the bot has already started,
        and the request would change the set of enabled capabilities.

        If the capability is not prefixed, and no other plugin prohibits it, it
        will be requested. Otherwise, it will not be requested. Since
        capability requests that are not mandatory may be rejected by the
        server, as well as by other plugins, a plugin which makes such a
        request should account for that possibility.

        The actual capability request to the server is handled after the
        completion of this function. In the event that the server denies a
        request, the ``failure_callback`` function will be called, if provided.
        The arguments will be a :class:`~sopel.bot.Sopel` object, and the
        capability which was rejected. This can be used to disable callables
        which rely on the capability. It will be be called either if the server
        NAKs the request, or if the server enabled it and later DELs it.

        The ``success_callback`` function will be called upon acknowledgment
        of the capability from the server, whether during the initial
        capability negotiation, or later.

        If ``arg`` is given, and does not exactly match what the server
        provides or what other plugins have requested for that capability, it
        is considered a conflict.
        """
        # TODO raise better exceptions
        cap = capability[1:]
        prefix = capability[0]

        entry = self._cap_reqs.get(cap, [])
        if any((ent.arg != arg for ent in entry)):
            raise Exception('Capability conflict')

        if prefix == '-':
            if self.connection_registered and cap in self.enabled_capabilities:
                raise Exception('Can not change capabilities after server '
                                'connection has been completed.')
            if any((ent.prefix != '-' for ent in entry)):
                raise Exception('Capability conflict')
            entry.append(CapReq(prefix, plugin_name, failure_callback, arg,
                                success_callback))
            self._cap_reqs[cap] = entry
        else:
            if prefix != '=':
                cap = capability
                prefix = ''
            if self.connection_registered and (cap not in
                                               self.enabled_capabilities):
                raise Exception('Can not change capabilities after server '
                                'connection has been completed.')
            # Non-mandatory will callback at the same time as if the server
            # rejected it.
            if any((ent.prefix == '-' for ent in entry)) and prefix == '=':
                raise Exception('Capability conflict')
            entry.append(CapReq(prefix, plugin_name, failure_callback, arg,
                                success_callback))
            self._cap_reqs[cap] = entry

    def write(self, args: Iterable[str], text: Optional[str] = None) -> None:
        """Send a command to the server.

        :param args: an iterable of strings, which will be joined by spaces
        :param text: a string that will be prepended with a ``:`` and added to
                     the end of the command

        ``args`` is an iterable of strings, which are joined by spaces.
        ``text`` is treated as though it were the final item in ``args``, but
        is preceded by a ``:``. This is a special case which means that
        ``text``, unlike the items in ``args``, may contain spaces (though this
        constraint is not checked by ``write``).

        In other words, both ``sopel.write(('PRIVMSG',), 'Hello, world!')``
        and ``sopel.write(('PRIVMSG', ':Hello, world!'))`` will send
        ``PRIVMSG :Hello, world!`` to the server.

        Newlines and carriage returns (``'\\n'`` and ``'\\r'``) are removed
        before sending. Additionally, if the message (after joining) is longer
        than than 510 characters, any remaining characters will not be sent.

        .. seealso::

            The connection backend is responsible for formatting and sending
            the message through the IRC connection. See the
            :meth:`sopel.irc.abstract_backends.AbstractIRCBackend.send_command`
            method for more information.

        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        args = [safe(arg) for arg in args]
        self.backend.send_command(*args, text=text)

    # IRC Commands

    def action(self, text: str, dest: str) -> None:
        """Send a CTCP ACTION PRIVMSG to a user or channel.

        :param str text: the text to send in the CTCP ACTION
        :param str dest: the destination of the CTCP ACTION

        The same loop detection and length restrictions apply as with
        :func:`say`, though automatic message splitting is not available.
        """
        self.say('\001ACTION {}\001'.format(text), dest)

    def join(self, channel: str, password: Optional[str] = None) -> None:
        """Join a ``channel``.

        :param str channel: the channel to join
        :param str password: an optional channel password

        If ``channel`` contains a space, and no ``password`` is given, the
        space is assumed to split the argument into the channel to join and its
        password. ``channel`` should not contain a space if ``password``
        is given.
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self.backend.send_join(channel, password=password)

    def kick(
        self,
        nick: str,
        channel: str,
        text: Optional[str] = None,
    ) -> None:
        """Kick a ``nick`` from a ``channel``.

        :param nick: nick to kick out of the ``channel``
        :param channel: channel to kick ``nick`` from
        :param text: optional text for the kick

        The bot must be an operator in the specified channel for this to work.

        .. versionadded:: 7.0
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self.backend.send_kick(channel, nick, reason=text)

    def notice(self, text: str, dest: str) -> None:
        """Send an IRC NOTICE to a user or channel (``dest``).

        :param text: the text to send in the NOTICE
        :param dest: the destination of the NOTICE
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self.backend.send_notice(dest, text)

    def part(self, channel: str, msg: Optional[str] = None) -> None:
        """Leave a channel.

        :param channel: the channel to leave
        :param msg: the message to display when leaving a channel
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self.backend.send_part(channel, reason=msg)

    def quit(self, message: Optional[str] = None) -> None:
        """Disconnect from IRC and close the bot.

        :param message: optional QUIT message to send (e.g. "Bye!")
        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        self.backend.send_quit(reason=message)
        self.hasquit = True
        # Wait for acknowledgment from the server. Per RFC 2812 it should be
        # an ERROR message, but many servers just close the connection.
        # Either way is fine by us. Closing the connection now would mean that
        # stuff in the buffers that has not yet been processed would never be
        # processed. It would also release the main thread, which is
        # problematic because whomever called quit might still want to do
        # something before the main thread quits.

    def restart(self, message: Optional[str] = None) -> None:
        """Disconnect from IRC and restart the bot.

        :param message: optional QUIT message to send (e.g. "Be right back!")
        """
        self.wantsrestart = True
        self.quit(message)

    def reply(
        self,
        text: str,
        dest: str,
        reply_to: str,
        notice: bool = False,
    ) -> None:
        """Send a PRIVMSG to a user or channel, prepended with ``reply_to``.

        :param text: the text of the reply
        :param dest: the destination of the reply
        :param reply_to: the nickname that the reply will be prepended with
        :param notice: whether to send the reply as a ``NOTICE`` or not,
                       defaults to ``False``

        If ``notice`` is ``True``, send a ``NOTICE`` rather than a ``PRIVMSG``.

        The same loop detection and length restrictions apply as with
        :meth:`say`, though automatic message splitting is not available.
        """
        text = '%s: %s' % (reply_to, text)
        if notice:
            self.notice(text, dest)
        else:
            self.say(text, dest)

    def say(
        self,
        text: str,
        recipient: str,
        max_messages: int = 1,
        truncation: str = '',
        trailing: str = '',
    ) -> None:
        """Send a ``PRIVMSG`` to a user or channel.

        :param text: the text to send
        :param recipient: the message recipient
        :param max_messages: split ``text`` into at most this many messages
                             if it is too long to fit in one (optional)
        :param truncation: string to append if ``text`` is too long to fit in
                           a single message, or into the last message if
                           ``max_messages`` is greater than 1 (optional)
        :param trailing: string to append after ``text`` and (if used)
                         ``truncation`` (optional)

        By default, this will attempt to send the entire ``text`` in one
        message. If the text is too long for the server, it may be truncated.

        If ``max_messages`` is given, the ``text`` will be split into at most
        that many messages. The split is made at the last space character
        before the "safe length" (which is calculated based on the bot's
        nickname and hostmask), or exactly at the "safe length" if no such
        space character exists.

        If the ``text`` is too long to fit into the specified number of messages
        using the above splitting, the final message will contain the entire
        remainder, which may be truncated by the server. You can specify
        ``truncation`` to tell Sopel how it should indicate that the remaining
        ``text`` was cut off. Note that the ``truncation`` parameter must
        include leading whitespace if you desire any between it and the
        truncated text.

        The ``trailing`` parameter is *always* appended to ``text``, after the
        point where ``truncation`` would be inserted if necessary. It's useful
        for making sure e.g. a link is always included, even if the summary your
        plugin fetches is too long to fit.

        Here are some examples of how the ``truncation`` and ``trailing``
        parameters work, using an artificially low maximum line length::

            # bot.say() outputs <text> + <truncation?> + <trailing>
            #                   always     if needed       always

            bot.say(
                '"This is a short quote.',
                truncation=' […]',
                trailing='"')
            # Sopel says: "This is a short quote."

            bot.say(
                '"This quote is very long and will not fit on a line.',
                truncation=' […]',
                trailing='"')
            # Sopel says: "This quote is very long […]"

            bot.say(
                # note the " included at the end this time
                '"This quote is very long and will not fit on a line."',
                truncation=' […]')
            # Sopel says: "This quote is very long […]
            # The ending " goes missing

        .. versionadded:: 7.1

            The ``truncation`` and ``trailing`` parameters.

        """
        if self.backend is None:
            raise RuntimeError(ERR_BACKEND_NOT_INITIALIZED)

        excess = ''
        safe_length = self.safe_text_length(recipient)

        if not isinstance(text, str):
            # Make sure we are dealing with a Unicode string
            text = text.decode('utf-8')

        if max_messages > 1 or truncation or trailing:
            if max_messages == 1 and trailing:
                safe_length -= len(trailing.encode('utf-8'))
            text, excess = tools.get_sendable_message(text, safe_length)

        if max_messages == 1:
            if excess and truncation:
                # only append `truncation` if this is the last message AND it's still too long
                safe_length -= len(truncation.encode('utf-8'))
                text, excess = tools.get_sendable_message(text, safe_length)
                text += truncation

            # ALWAYS append `trailing`;
            # its length is included when determining if truncation happened above
            text += trailing

        flood_max_wait = self.settings.core.flood_max_wait
        flood_burst_lines = self.settings.core.flood_burst_lines
        flood_refill_rate = self.settings.core.flood_refill_rate
        flood_empty_wait = self.settings.core.flood_empty_wait

        flood_text_length = self.settings.core.flood_text_length
        flood_penalty_ratio = self.settings.core.flood_penalty_ratio

        antiloop_threshold = min(10, self.settings.core.antiloop_threshold)
        antiloop_window = self.settings.core.antiloop_window
        antiloop_repeat_text = self.settings.core.antiloop_repeat_text
        antiloop_silent_after = self.settings.core.antiloop_silent_after

        with self.sending:
            recipient_id = self.make_identifier(recipient)
            recipient_stack = self.stack.setdefault(recipient_id, {
                'messages': deque(maxlen=10),
                'flood_left': flood_burst_lines,
            })

            if recipient_stack['messages']:
                elapsed = time.time() - recipient_stack['messages'][-1][0]
            else:
                # Default to a high enough value that we won't care.
                # Five minutes should be enough not to matter anywhere below.
                elapsed = 300

            # If flood bucket is empty, refill the appropriate number of lines
            # based on how long it's been since our last message to recipient
            if not recipient_stack['flood_left']:
                recipient_stack['flood_left'] = min(
                    flood_burst_lines,
                    int(elapsed) * flood_refill_rate)

            # If it's too soon to send another message, wait
            if not recipient_stack['flood_left']:
                penalty = 0

                if flood_penalty_ratio > 0:
                    penalty_ratio = flood_text_length * flood_penalty_ratio
                    text_length_overflow = float(
                        max(0, len(text) - flood_text_length))
                    penalty = text_length_overflow / penalty_ratio

                # Maximum wait time is 2 sec by default
                initial_wait_time = flood_empty_wait + penalty
                wait = min(initial_wait_time, flood_max_wait)
                if elapsed < wait:
                    sleep_time = wait - elapsed
                    LOGGER.debug(
                        'Flood protection wait time: %.3fs; '
                        'elapsed time: %.3fs; '
                        'initial wait time (limited to %.3fs): %.3fs '
                        '(including %.3fs of penalty).',
                        sleep_time,
                        elapsed,
                        flood_max_wait,
                        initial_wait_time,
                        penalty,
                    )
                    time.sleep(sleep_time)

            # Loop detection
            if antiloop_threshold > 0 and elapsed < antiloop_window:
                messages = [m[1] for m in recipient_stack['messages']]

                # If what we're about to send repeated at least N times
                # in the anti-looping window, replace it
                if messages.count(text) >= antiloop_threshold:
                    text = antiloop_repeat_text
                    if messages.count(text) >= antiloop_silent_after:
                        # If we've already said that N times, discard message
                        return

            self.backend.send_privmsg(recipient, text)

            # update recipient metadata
            flood_left = recipient_stack['flood_left'] - 1
            recipient_stack['flood_left'] = max(0, flood_left)
            recipient_stack['messages'].append((time.time(), safe(text)))

        # Now that we've sent the first part, we need to send the rest if
        # requested. Doing so recursively seems simpler than iteratively.
        if max_messages > 1 and excess:
            self.say(excess, recipient, max_messages - 1, truncation, trailing)
