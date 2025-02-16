import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv
import random
import datetime

# Load environment variables
load_dotenv()

# Define a custom client that supports slash commands.
class MyClient(discord.Client):
    def __init__(self):
        # Enable all intents for more functionality
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # This syncs your slash commands with Discord.
        await self.tree.sync()
        
    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="/help"))
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

client = MyClient()

# Utility Commands
@client.tree.command(name="ping", description="Check the bot's latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Pong! 🏓\nLatency: {round(client.latency * 1000)}ms"
    )

@client.tree.command(name="serverinfo", description="Get information about the server")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    
    # Get owner info safely
    owner = "Unknown"
    if guild.owner:
        owner = guild.owner.mention
    elif guild.owner_id:
        owner = f"<@{guild.owner_id}>"

    embed = discord.Embed(
        title=f"📊 {guild.name} Info",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    
    # Set thumbnail safely
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
        
    embed.add_field(name="Owner", value=owner, inline=True)
    embed.add_field(name="Created On", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Member Count", value=guild.member_count, inline=True)
    embed.add_field(name="Boost Level", value=f"Level {guild.premium_tier}", inline=True)
    embed.add_field(name="Channels", value=f"💬 {len(guild.text_channels)} | 🔊 {len(guild.voice_channels)}", inline=True)
    embed.add_field(name="Roles", value=len(guild.roles), inline=True)
    
    await interaction.response.send_message(embed=embed)

# Fun Commands
@client.tree.command(name="roll", description="Roll a dice")
@app_commands.describe(sides="Number of sides on the dice (default: 6)")
async def roll(interaction: discord.Interaction, sides: int = 6):
    if sides < 2:
        await interaction.response.send_message("A dice must have at least 2 sides!", ephemeral=True)
        return
    result = random.randint(1, sides)
    await interaction.response.send_message(f"🎲 You rolled a {result} (d{sides})")

@client.tree.command(name="8ball", description="Ask the magic 8-ball a question")
@app_commands.describe(question="Your yes/no question for the 8-ball")
async def eightball(interaction: discord.Interaction, question: str):
    responses = [
        "It is certain.", "It is decidedly so.", "Without a doubt.",
        "Yes - definitely.", "You may rely on it.", "As I see it, yes.",
        "Most likely.", "Outlook good.", "Yes.", "Signs point to yes.",
        "Reply hazy, try again.", "Ask again later.", "Better not tell you now.",
        "Cannot predict now.", "Concentrate and ask again.",
        "Don't count on it.", "My reply is no.", "My sources say no.",
        "Outlook not so good.", "Very doubtful."
    ]
    embed = discord.Embed(
        title="🎱 Magic 8-Ball",
        color=discord.Color.purple()
    )
    embed.add_field(name="Question", value=question, inline=False)
    embed.add_field(name="Answer", value=random.choice(responses), inline=False)
    
    await interaction.response.send_message(embed=embed)

# Moderation Commands
@client.tree.command(name="clear", description="Clear messages from the channel")
@app_commands.describe(amount="Number of messages to clear (default: 5)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: int = 5):
    if amount < 1:
        await interaction.response.send_message("Please specify a positive number!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=amount)
    await interaction.followup.send(f"✨ Cleared {len(deleted)} messages!", ephemeral=True)

@client.tree.command(name="userinfo", description="Get information about a user")
@app_commands.describe(user="The user to get information about")
async def userinfo(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    roles = [role.mention for role in user.roles[1:]]  # All roles except @everyone
    
    embed = discord.Embed(
        title=f"👤 User Information - {user.name}",
        color=user.color,
        timestamp=datetime.datetime.now()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    embed.add_field(name="ID", value=user.id, inline=True)
    embed.add_field(name="Nickname", value=user.nick if user.nick else "None", inline=True)
    embed.add_field(name="Account Created", value=user.created_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name="Joined Server", value=user.joined_at.strftime("%Y-%m-%d"), inline=True)
    embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles) if roles else "None", inline=False)
    
    await interaction.response.send_message(embed=embed)

client.run(os.getenv('DISCORD_TOKEN'))