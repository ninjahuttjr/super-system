import discord
from discord.ext import commands
from discord import app_commands
import os
from dotenv import load_dotenv
import random
import datetime
from typing import Dict, List, Optional
import json
import asyncio
import openai
import aiohttp
import io
import base64
from PIL import Image
import logging
from openai import OpenAI

# Load environment variables
load_dotenv()

# Setup OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

# Set up logging
logger = logging.getLogger('AdventureGame')
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(name)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

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

class Player:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.location = None
        self.inventory = []
        self.health = 100
        self.gold = 0
        self.history = []

class AdventureGame:
    def __init__(self):
        self.players = {}  # Will store Player objects
        self.locations = {}
        self.comfy_ws_url = "ws://127.0.0.1:8188/ws"
        
    def get_player(self, user_id: int) -> Player:
        """Get a player by their ID, or create a new one if they don't exist"""
        if user_id not in self.players:
            self.players[user_id] = Player(user_id)
        return self.players[user_id]

    def get_location(self, location_id: str) -> Optional[Dict]:
        """Get a location by its ID"""
        return self.locations.get(location_id)

    async def generate_location_image(self, description: str) -> bytes:
        """Generate an image using ComfyUI with Flux workflow"""
        try:
            # Create output directory if it doesn't exist
            os.makedirs(os.path.join("ComfyUI", "output"), exist_ok=True)
            
            logger.info(f"Starting image generation for description: {description}")
            
            workflow = {
                "5": {
                    "inputs": {
                        "width": ["70", 0],
                        "height": ["71", 0],
                        "batch_size": 1
                    },
                    "class_type": "EmptyLatentImage"
                },
                "6": {
                    "inputs": {
                        "text": ["125", 0],
                        "clip": ["116", 1]
                    },
                    "class_type": "CLIPTextEncode"
                },
                "8": {
                    "inputs": {
                        "samples": ["13", 0],
                        "vae": ["10", 0]
                    },
                    "class_type": "VAEDecode"
                },
                "9": {
                    "inputs": {
                        "filename_prefix": "ComfyUI",
                        "images": ["8", 0]
                    },
                    "class_type": "SaveImage"
                },
                "10": {
                    "inputs": {
                        "vae_name": "flux1DevVAE_safetensors.safetensors"
                    },
                    "class_type": "VAELoader"
                },
                "11": {
                    "inputs": {
                        "clip_name1": "t5xxl_fp16.safetensors",
                        "clip_name2": "clip_l.safetensors",
                        "type": "flux"
                    },
                    "class_type": "DualCLIPLoader"
                },
                "12": {
                    "inputs": {
                        "unet_name": "flux1-dev.safetensors",
                        "weight_dtype": "default"
                    },
                    "class_type": "UNETLoader"
                },
                "13": {
                    "inputs": {
                        "noise": ["25", 0],
                        "guider": ["22", 0],
                        "sampler": ["16", 0],
                        "sigmas": ["17", 0],
                        "latent_image": ["5", 0]
                    },
                    "class_type": "SamplerCustomAdvanced"
                },
                "16": {
                    "inputs": {
                        "sampler_name": "euler"
                    },
                    "class_type": "KSamplerSelect"
                },
                "17": {
                    "inputs": {
                        "scheduler": "simple",
                        "steps": 20,
                        "denoise": 1,
                        "model": ["61", 0]
                    },
                    "class_type": "BasicScheduler"
                },
                "22": {
                    "inputs": {
                        "model": ["61", 0],
                        "conditioning": ["60", 0]
                    },
                    "class_type": "BasicGuider"
                },
                "25": {
                    "inputs": {
                        "noise_seed": random.randint(1, 999999999)
                    },
                    "class_type": "RandomNoise"
                },
                "60": {
                    "inputs": {
                        "guidance": 3.5,
                        "conditioning": ["6", 0]
                    },
                    "class_type": "FluxGuidance"
                },
                "61": {
                    "inputs": {
                        "max_shift": 0.5,
                        "base_shift": 0.5,
                        "width": ["70", 0],
                        "height": ["71", 0],
                        "model": ["116", 0]
                    },
                    "class_type": "ModelSamplingFlux"
                },
                "70": {
                    "inputs": {
                        "int": 1024
                    },
                    "class_type": "Int Literal"
                },
                "71": {
                    "inputs": {
                        "int": 1024
                    },
                    "class_type": "Int Literal"
                },
                "116": {
                    "inputs": {
                        "model": ["12", 0],
                        "clip": ["11", 0]
                    },
                    "class_type": "Power Lora Loader (rgthree)"
                },
                "125": {
                    "inputs": {
                        "string": description
                    },
                    "class_type": "String Literal"
                }
            }
            
            async with aiohttp.ClientSession() as session:
                # Queue the prompt
                logger.info("Queueing prompt with ComfyUI")
                async with session.post('http://127.0.0.1:8188/prompt', json={"prompt": workflow}) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to queue prompt: {error_text}")
                        raise Exception(f"Failed to queue prompt: {error_text}")
                        
                    prompt_data = await response.json()
                    prompt_id = prompt_data['prompt_id']
                    logger.info(f"Prompt queued successfully with ID: {prompt_id}")
                
                # Connect to websocket
                logger.info(f"Connecting to WebSocket at {self.comfy_ws_url}")
                async with session.ws_connect(self.comfy_ws_url) as websocket:
                    while True:
                        try:
                            msg = await websocket.receive()
                            
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                
                                if data["type"] == "progress":
                                    value = data.get("data", {}).get("value", 0)
                                    max_value = data.get("data", {}).get("max", 100)
                                    print(f"Progress: {value}/{max_value}")
                                    logger.info(f"Generation progress: {value}/{max_value}")
                                    
                                    # When we hit step 20, that's our signal!
                                    if value == 20:
                                        logger.info("Generation complete! Getting image...")
                                        await asyncio.sleep(2.0)  # Wait for file to be written
                                        
                                        output_dir = r"C:\Users\HawkAdmin\Desktop\Comfy\ComfyUI_windows_portable\ComfyUI\output"
                                        logger.info(f"Checking directory: {output_dir}")
                                        
                                        try:
                                            files = [f for f in os.listdir(output_dir) if f.startswith('ComfyUI_') and f.endswith('.png')]
                                            logger.info(f"Found files: {files}")
                                            
                                            if files:
                                                latest_file = max([os.path.join(output_dir, f) for f in files], key=os.path.getmtime)
                                                logger.info(f"Using latest file: {latest_file}")
                                                with open(latest_file, 'rb') as f:
                                                    image_bytes = f.read()
                                                    logger.info(f"Successfully read image file: {latest_file} ({len(image_bytes)} bytes)")
                                                    return image_bytes
                                            else:
                                                logger.error("No ComfyUI_ files found in output directory")
                                        except Exception as e:
                                            logger.error(f"Error accessing output directory: {str(e)}")
                                        break
                            
                        except Exception as e:
                            logger.error(f"Error processing message: {str(e)}")
                            break

            return None
            
        except Exception as e:
            logger.error(f"Error in generate_location_image: {str(e)}")
            return None

    async def generate_location(self, context: str, player: Player) -> Dict:
        """Generate a new location using GPT-4"""
        try:
            client = OpenAI()
            
            # Include player history in the context
            history_context = "\n".join(player.history[-3:]) if player.history else ""
            prompt = f"""Generate a fantasy location with context: {context}
                        Player's recent history: {history_context}"""
            
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a creative fantasy game location generator. Output must be valid JSON with fields: id (string), name (string), description (string), exits (object with direction strings as keys and location IDs as values), and items (array of strings)."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7
            )
            
            location_data = json.loads(completion.choices[0].message.content)
            
            # Generate image
            image_prompt = f"fantasy location, {location_data['description']}, detailed, magical, 4k, high quality"
            image_data = await self.generate_location_image(image_prompt)
            if image_data:
                location_data["image"] = base64.b64encode(image_data).decode('utf-8')
            
            # Store location and update player history
            self.locations[location_data['id']] = location_data
            player.history.append(f"Visited {location_data['name']}: {location_data['description'][:100]}...")
            if len(player.history) > 5:
                player.history.pop(0)
                
            return location_data
            
        except Exception as e:
            logger.error(f"Error generating location: {e}")
            return None

class AdventureView(discord.ui.View):
    def __init__(self, game: AdventureGame, player: Player):
        super().__init__(timeout=180)
        self.game = game
        self.player = player
        self.update_buttons()
    
    def update_buttons(self):
        self.clear_items()
        location = self.game.locations.get(self.player.location)
        if not location:
            return
            
        # Direction buttons with emojis
        for direction, loc_id in location["exits"].items():
            emoji = {
                "north": "⬆️",
                "south": "⬇️",
                "east": "➡️",
                "west": "⬅️"
            }.get(direction.lower(), "🚶")
            
            button = discord.ui.Button(
                label=direction.title(),
                custom_id=f"move_{direction}",
                style=discord.ButtonStyle.primary,
                emoji=emoji
            )
            button.callback = self.move_callback
            self.add_item(button)
        
        # Action buttons
        if location["items"]:
            interact_button = discord.ui.Button(
                label="Search Area",
                custom_id="interact",
                style=discord.ButtonStyle.success,
                emoji="🔍"
            )
            interact_button.callback = self.interact_callback
            self.add_item(interact_button)
        
        # Character button
        char_button = discord.ui.Button(
            label="Character",
            custom_id="character",
            style=discord.ButtonStyle.secondary,
            emoji="👤"
        )
        char_button.callback = self.character_callback
        self.add_item(char_button)

    async def move_callback(self, interaction: discord.Interaction):
        direction = interaction.custom_id.split("_")[1]
        location = self.game.locations[self.player.location]
        
        await interaction.response.defer(thinking=True)
        
        if direction in location["exits"]:
            new_loc_id = location["exits"][direction]
            
            # Generate new location if needed
            if new_loc_id not in self.game.locations:
                context = f"Connected to {location['name']} from the {direction}"
                new_location = await self.game.generate_location(context, self.player)
                if not new_location:
                    await interaction.followup.send("Failed to generate new location!", ephemeral=True)
                    return
            
            self.player.location = new_loc_id
            await self.update_location_display(interaction)

    async def character_callback(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title=f"👤 {interaction.user.name}'s Character",
            color=discord.Color.gold()
        )
        
        # Add character stats
        embed.add_field(name="📍 Current Location", value=self.game.locations[self.player.location]["name"], inline=False)
        embed.add_field(name="🎒 Inventory", value="\n".join(self.player.inventory) if self.player.inventory else "Empty", inline=False)
        embed.add_field(name="📜 Recent History", value="\n".join(self.player.history[-3:]) if self.player.history else "No adventures yet", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def interact_callback(self, interaction: discord.Interaction):
        location = self.game.locations[self.player.location]
        
        if not location["items"]:
            await interaction.response.send_message("There's nothing interesting to interact with here.", ephemeral=True)
            return
            
        # Create select menu for items
        select = discord.ui.Select(
            placeholder="Choose an item to interact with...",
            options=[
                discord.SelectOption(
                    label=item,
                    description=f"Interact with {item}",
                    value=item
                ) for item in location["items"]
            ]
        )
        
        async def select_callback(interaction: discord.Interaction):
            item = select.values[0]
            location["items"].remove(item)
            self.player.inventory.append(item)
            await interaction.response.send_message(f"You picked up the {item}!", ephemeral=True)
            self.update_buttons()
            
        select.callback = select_callback
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.send_message("What would you like to interact with?", view=view, ephemeral=True)

    async def update_location_display(self, interaction: discord.Interaction):
        location = self.game.locations[self.player.location]
        
        embed = discord.Embed(
            title=f"🌟 {location['name']}",
            description=location['description'],
            color=discord.Color.blue()
        )
        
        files = []
        if "image" in location:
            image_bytes = base64.b64decode(location["image"])
            file = discord.File(io.BytesIO(image_bytes), filename="location.png")
            embed.set_image(url="attachment://location.png")
            files.append(file)
            
        embed.add_field(
            name="📍 Available Paths",
            value=" | ".join([f"→ {direction.title()}" for direction in location["exits"].keys()]),
            inline=False
        )
        
        if location["items"]:
            embed.add_field(
                name="👀 You Notice",
                value="\n".join([f"• {item}" for item in location["items"]]),
                inline=False
            )
            
        await interaction.followup.send(embed=embed, view=self, files=files)

client = MyClient()

# Create game instance after client creation
game = AdventureGame()

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

# Add these new commands after your existing commands
@client.tree.command(name="start_adventure", description="Start or continue your adventure")
async def start_adventure(interaction: discord.Interaction):
    # Create loading embed
    loading_embed = discord.Embed(
        title="🌟 Embarking on an Adventure",
        description="Preparing your journey into a magical realm...",
        color=discord.Color.blue()
    )
    loading_embed.add_field(
        name="Status",
        value="```\n🎨 Generating location...\n⌛ Crafting image...\n```",
        inline=False
    )
    loading_embed.set_footer(text="This may take a minute as we craft your unique experience...")
    
    await interaction.response.send_message(embed=loading_embed)
    
    player = game.get_player(interaction.user.id)
    
    if not player.location:
        location = await game.generate_location("Generate a starting hub location", player)
        if not location:
            error_embed = discord.Embed(
                title="❌ Adventure Creation Failed",
                description="Something went wrong while creating your adventure. Please try again.",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=error_embed)
            return
        player.location = location["id"]
    
    location = game.locations[player.location]
    
    # Create the main adventure embed
    embed = discord.Embed(
        title=f"🌟 {location['name']}",
        description=location['description'],
        color=discord.Color.blue()
    )
    
    # Prepare the image if we have one
    files = []
    if "image" in location:
        image_bytes = base64.b64decode(location["image"])
        file = discord.File(io.BytesIO(image_bytes), filename="location.png")
        embed.set_image(url="attachment://location.png")
        files.append(file)
    
    # Add location details
    embed.add_field(
        name="📍 Available Paths",
        value=" | ".join([f"→ {direction.title()}" for direction in location["exits"].keys()]),
        inline=False
    )
    
    if location["items"]:
        embed.add_field(
            name="👀 You Notice",
            value="\n".join([f"• {item}" for item in location["items"]]),
            inline=False
        )
    
    embed.set_footer(text="Use the buttons below to interact with your surroundings")
    
    view = AdventureView(game, player)
    await interaction.edit_original_response(
        content=None,
        embed=embed,
        view=view,
        attachments=files
    )

@client.tree.command(name="go", description="Move in a direction")
@app_commands.describe(direction="The direction to move (north, south, east, west)")
async def go(interaction: discord.Interaction, direction: str):
    player = game.get_player(interaction.user.id)
    current_location = game.get_location(player["location"])
    
    if direction not in current_location["exits"]:
        await interaction.response.send_message(f"You cannot go {direction} from here!", ephemeral=True)
        return
        
    player["location"] = current_location["exits"][direction]
    new_location = game.get_location(player["location"])
    
    embed = discord.Embed(
        title=f"🚶 Moving {direction} to {new_location['name']}",
        description=new_location["description"],
        color=discord.Color.blue()
    )
    embed.add_field(name="Exits", value=" | ".join(new_location["exits"].keys()), inline=False)
    embed.add_field(name="Items Here", value=" | ".join(new_location["items"]), inline=False)
    
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="take", description="Pick up an item")
@app_commands.describe(item="The item to pick up")
async def take(interaction: discord.Interaction, item: str):
    player = game.get_player(interaction.user.id)
    location = game.get_location(player["location"])
    
    if item not in location["items"]:
        await interaction.response.send_message(f"There is no {item} here to take!", ephemeral=True)
        return
        
    location["items"].remove(item)
    player["inventory"].append(item)
    
    await interaction.response.send_message(f"📦 You picked up the {item}!")

@client.tree.command(name="explore", description="Explore a new location")
async def explore(interaction: discord.Interaction, direction: str):
    await interaction.response.defer()
    
    player = game.get_player(interaction.user.id)
    current_location = game.get_location(player["location"])
    
    if direction not in current_location["exits"]:
        await interaction.followup.send(f"You cannot go {direction} from here!")
        return
        
    new_location_id = current_location["exits"][direction]
    
    # Generate or get the new location
    if new_location_id not in game.locations:
        new_location = await game.generate_location(new_location_id, player)
        if new_location:
            game.locations[new_location_id] = new_location
        else:
            await interaction.followup.send("Failed to generate location!")
            return
            
    new_location = game.locations[new_location_id]
    player["location"] = new_location_id
    
    # Create embed with location info and image
    embed = discord.Embed(
        title=new_location["name"],
        description=new_location["description"],
        color=discord.Color.blue()
    )

async def handle_command(message):
    if message.content.startswith('!generate'):
        await message.channel.send("Generating image... (this may take a minute)")
        
        try:
            image_path = await handle_image_generation(response_queue)
            if image_path:
                await message.channel.send(file=discord.File(image_path))
            else:
                await message.channel.send("Sorry, there was an error generating the image")
        except TimeoutError:
            await message.channel.send("Image generation timed out. Please try again.")

async def handle_message(message_type, message_data):
    if message_type == WSMsgType.BINARY:
        # Handle binary image data
        try:
            # Save or process the binary image data
            with open("received_image.jpg", "wb") as f:
                f.write(message_data)
            return "Image received and saved successfully"
        except Exception as e:
            return f"Error saving image: {str(e)}"
    elif message_type == WSMsgType.TEXT:
        # Handle text messages
        return "Received text message: " + message_data
    else:
        return f"Unsupported message type: {message_type}"

client.run(os.getenv('DISCORD_TOKEN'))