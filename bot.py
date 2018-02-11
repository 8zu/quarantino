import asyncio
import logging
import os.path as osp
import sys
import random
from collections import namedtuple

import pytoml

from cache import Cache

Command = namedtuple('Command', ['rank', 'add'])

try:
    from discord.ext import commands
    from discord import utils
    import discord
except ImportError:
    print("Discord.py is not installed.\n"
          "Consult the guide for your operating system "
          "and do ALL the steps in order.\n"
          "https://twentysix26.github.io/Red-Docs/\n")
    sys.exit(1)

config_path = './config.toml'
description = """A simple mod bot"""

def get_config(path):
    if osp.exists(path):
        config = pytoml.load(open(path, "r", encoding="UTF-8"))
    else:
        logger.error("Missing config file! Shutting down now...")
        sys.exit(1)

    return config

class Quarantino(commands.Bot):
    def __init__(self, cache, texts):
        super().__init__(description=description, command_prefix='+')
        self.cache = cache
        self.texts = texts
        self.initialized = False

    def is_me(self, author):
        return author == self.user

    def is_owner(self, author):
        return self.server and author == self.server.owner

    def is_command(self, cmd, s):
        return s.strip().split(' ')[0] == f"{self.command_prefix}{cmd}"

    def like_command(self, cmd, s):
        return s.strip().split(' ')[0].startswith(f"{self.command_prefix}{cmd}")

    def find_role(self, name):
        roles = self.server.role_hierarchy
        return utils.find(lambda r: r.name == name, roles)

    def find_channel(self, name):
        return utils.find(lambda ch: ch.name == name, self.get_all_channels())

    def resume(self, cache, config):
        if self.initialized:
            return

        self.server = cache.load('server.json').get_or(None)
        if not self.server:
            print("The bot has not joined a server, initialization incomplete")
            return False
        self.server = self.get_server(self.server)

        self.application_channel = self.find_channel(config['application_channel'])
        if not self.application_channel:
            return FAlse
        self.eligible_role = self.find_role(config['eligible_role'])
        if not self.eligible_role:
            return False
        self.subscription_channel = self.find_channel(config['subscription_channel'])
        if not self.subscription_channel:
            return False
        self.vetting_room = {}
        self.ranks = config['ranks']

        self.yes_words = config['yes_words']
        self.no_words = config['no_words']

        print("Initialization complete")
        self.initialized = True
        return True

    def check_eligible(self, user):
        return self.eligible_role in user.roles

    def parse_command(self, s):
        if s[0] in ['+', '-']:
            return Command(s[1:], s[0] == '+')
        else:
            None

    async def greet(self, member):
        msg_text = self.texts['greeting'].format(user=member.id, server=self.server.name, yes=self.yes_words[0])
        return await self.send_message(self.application_channel, msg_text)

    def add_to_vetting(self, member, greet_msg):
        self.vetting_room[member.id] = [greet_msg]

    def append_msg_to_vetting(self, member, msg):
        self.vetting_room[member.id].append(msg)

    def remove_from_vetting(self, member):
        self.vetting_room.pop(member.id, None)

    def is_vetting(self, member):
        return member.id in self.vetting_room

    def process_answer(self, s):
        s = s.strip().lower()
        if any(s.startswith(yes) for yes in self.yes_words):
            return True
        if any(s.startswith(no) for no in self.no_words):
            return False
        return None

    async def cleanup_after(self, reply, member):
        await self.delete_messages([reply, *self.vetting_room[member.id]])

    async def make_eligible(self, user):
        await self.add_roles(user, self.eligible_role)

    async def add_rank(self, user, rank):
        await self.add_roles(user, self.find_role(rank))

    async def remove_rank(self, user, rank):
        await self.remove_roles(user, self.find_role(rank))

def initialize(config):
    cache = Cache(config['cache_root'])
    texts = get_config(config['text_path'])
    bot = Quarantino(cache, texts)

    @bot.event
    async def on_ready():
        bot.resume(cache, config)
        if bot.initialized:
            print(f'Server: {bot.server.name}')
            print(f'Application channel: #{bot.application_channel.name}')
            print(f'Subscription channel: #{bot.subscription_channel.name}')
            print(f'Eligible role: @{bot.eligible_role.name}')
            print()
            print('Available ranks:')
            for rank in bot.ranks:
                print(f"\t{rank}")

    @bot.event
    async def on_server_join(server):
        print(f"I joined server {server.name}")
        cache.save("server.json", server.id)
        if not bot.resume(cache, config):
            print('There is something wrong with the initialization')

    @bot.event
    async def on_server_remove(server):
        print("I am kicked from server {server.name}")
        cache.purge('server.json')

    @bot.event
    async def on_member_join(member):
        greet_msg = await bot.greet(member)
        bot.add_to_vetting(member, greet_msg)

    @bot.event
    async def on_member_remove(member):
        if bot.is_vetting(member):
            bot.remove_from_vetting(member)

    @bot.event
    async def on_message(msg):
        if bot.is_me(msg.author):
            return
        if not bot.initialized:
            return

        async def say(msg_id, **kwargs):
            return await bot.send_message(msg.channel, texts[msg_id].format(**kwargs))

        if msg.channel.is_private:
            pass
        elif msg.channel == bot.application_channel:
            if bot.is_vetting(msg.author):
                ans = bot.process_answer(msg.content)
                if ans is True:
                    await bot.make_eligible(msg.author)
                    await bot.cleanup_after(msg, msg.author)
                    bot.remove_from_vetting(msg.author)
                elif ans is False:
                    await bot.cleanup_after(msg, msg.author)
                    await bot.kick(msg.author)
                    bot.remove_from_vetting(msg.author)
                else:
                    warn = await say('cannot_understand', user=msg.author.id, yes=bot.yes_words[0], no=bot.no_words[0])
                    bot.append_msg_to_vetting(msg.author, warn)
                    await bot.delete_message(msg)
        elif msg.channel == bot.subscription_channel:
            if not bot.check_eligible(msg.author):
                say('ineligible')
                return
            cmd = bot.parse_command(msg.content)
            if not cmd or not cmd.rank:
                return
            if cmd.rank in bot.ranks:
                if cmd.add:
                    await bot.add_rank(msg.author, cmd.rank)
                    await say('add_rank_response', user=msg.author.id, rank=cmd.rank)
                else:
                    await bot.remove_rank(msg.author, cmd.rank)
                    await say('remove_rank_response', user=msg.author.id, rank=cmd.rank)
            else:
                await say('rank_not_found', ranks=bot.ranks)


    return bot


if __name__ == '__main__':
    config = get_config(config_path)
    if 'token' not in config or not config['token']:
        logger.error("Token is not filled in! Shutting down now...")
        sys.exit(1)
    border_bot = initialize(config)
    border_bot.run(config['token'])
