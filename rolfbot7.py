import os
import openai
import discord
from discord.ext import commands
import asyncio
import random
from discord import FFmpegPCMAudio
import youtube_dl
from discord import VoiceClient
from discord.ext.commands import CommandError

openai.api_key = os.getenv('OPENAI_API_KEY')

intents = discord.Intents.all()
intents.members = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix='/', intents=intents)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: youtube_dl.YoutubeDL({'format': 'bestaudio'}).extract_info(url, download=not stream))

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = data['url'] if stream else youtube_dl.YoutubeDL({'outtmpl': './'}).download([data['webpage_url']])
        return cls(discord.FFmpegPCMAudio(filename, before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"), data=data)
       
@bot.command()
async def play(ctx, *, url):
    """Plays from a url (almost anything youtube_dl supports)"""
    
    # Replace 'channel_id' with the ID of your "music-request" channel
    music_request_channel_id = 1105528775292231711

    # If the command is not used in the "music-request" channel, ignore it
    if ctx.channel.id != music_request_channel_id:
        return
    async with ctx.typing():
        try:
        player = await YTDLSource.from_url(url, loop=bot.loop, stream=True)
        ctx.voice_client.play(player, after=lambda e: print(f'Player error: {e}') if e else None)
    except Exception as e:
        print(f'Error occurred: {e}')  # print error to console
        await ctx.send(f'An error occurred: {e}')  # send error to Discord
    else:
        await ctx.send('Now playing: {}'.format(player.title))

@play.before_invoke
async def ensure_voice(ctx):
    if ctx.voice_client is None:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            raise CommandError("You are not connected to a voice channel.")
    elif ctx.voice_client.is_playing():
        ctx.voice_client.stop()

async def on_ready():
    bot.loop.create_task(send_periodic_message())

async def send_periodic_message():
    while True:
        await asyncio.sleep(random.randint(2*24*60*60, 7*24*60*60))
        prompt = [
            {"role": "system", "content": "You are Rolfbot, a part of Rolf 2.0 which is our friend group. Your goal is to provide useful and fun responses."},
            {"role": "user", "content": "Make a casual, friendly comment about the weather."}
        ]
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=prompt,
            max_tokens=300,
            n=1,
            stop=None,
            temperature=1,
        )
        result = ''
        for choice in response.choices:
            result += choice.message['content']
        channel = bot.get_channel(356331666085642242)
        await channel.send(result.strip())


@bot.command()
async def rolfbot(ctx, *, message):
    prompt = [
        {"role": "system", "content": "You are Rolfbot, a part of Rolf 2.0 which is our friend group. Your goal is to provide useful and fun responses."},
        {"role": "user", "content": message}
    ]
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=prompt,
        max_tokens=2048,
        n=1,
        stop=None,
        temperature=0.7,
    )
    result = ''
    for choice in response.choices:
        result += choice.message['content']
    sender = ctx.message.author.mention
    await ctx.send(f"Hello {sender}, {result.strip()}")

bot.run(os.getenv('DISCORD_BOT_TOKEN'))