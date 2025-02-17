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
        self.active_message_id = None  # Store the active message ID
        self.channel_id = None  # Store the channel where the game is active
        self.health = 100
        self.gold = 0
        self.history = []

class AdventureGame:
    def __init__(self):
        self.players = {}  # Will store Player objects
        self.locations = {}
        self.active_messages = {}  # Store message IDs for active game sessions
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
                        "filename_prefix": "AdventureBot_",
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
                logger.info("Queueing prompt with ComfyUI")
                async with session.post('http://127.0.0.1:8188/prompt', json={"prompt": workflow}) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Failed to queue prompt: {error_text}")
                        raise Exception(f"Failed to queue prompt: {error_text}")
                        
                    prompt_data = await response.json()
                    our_prompt_id = prompt_data['prompt_id']
                    logger.info(f"Prompt queued successfully with ID: {our_prompt_id}")

                # Poll the history endpoint until our prompt is complete
                while True:
                    async with session.get('http://127.0.0.1:8188/history') as response:
                        if response.status == 200:
                            history = await response.json()
                            if our_prompt_id in history:
                                prompt_info = history[our_prompt_id]
                                logger.debug(f"Prompt status: {prompt_info}")
                                
                                if 'outputs' in prompt_info and '9' in prompt_info['outputs']:  # Node 9 is our SaveImage node
                                    logger.info("Generation complete! Getting image...")
                                    await asyncio.sleep(2.0)  # Wait for file to be written
                                    
                                    output_dir = r"C:\Users\HawkAdmin\Desktop\Comfy\ComfyUI_windows_portable\ComfyUI\output"
                                    logger.info(f"Checking directory: {output_dir}")
                                    
                                    try:
                                        files = [f for f in os.listdir(output_dir) if f.startswith('AdventureBot_') and f.endswith('.png')]
                                        logger.info(f"Found files: {files}")
                                        
                                        if files:
                                            latest_file = max([os.path.join(output_dir, f) for f in files], key=os.path.getmtime)
                                            logger.info(f"Using latest file: {latest_file}")
                                            with open(latest_file, 'rb') as f:
                                                image_bytes = f.read()
                                                logger.info(f"Successfully read image file: {latest_file} ({len(image_bytes)} bytes)")
                                                return image_bytes
                                        else:
                                            logger.error("No AdventureBot_ files found in output directory")
                                    except Exception as e:
                                        logger.error(f"Error accessing output directory: {str(e)}")
                                    break
                
                    await asyncio.sleep(1.0)  # Wait a second before polling again
                            
        except Exception as e:
            logger.error(f"Error in generate_location_image: {str(e)}")
            raise

    async def generate_location(self, context: str, player: Player) -> Dict:
        try:
            client = OpenAI()
            
            system_prompt = """You are generating locations for a dark fantasy RPG aimed at adult players. 
            Create gritty, mature locations with:
            - Realistic, harsh environments
            - Dark fantasy elements (think Dark Souls, The Witcher)
            - Context-specific paths and actions (not just cardinal directions)
            - Brief, impactful descriptions (2-3 sentences max)
            - Practical items and interactive elements
            
            Output must be valid JSON with fields: 
            id (string), 
            name (string), 
            description (string), 
            paths (array of objects with: {
                "name": "descriptive name of path/action",
                "description": "brief description of what this path/action means",
                "target_id": "location_id of destination"
            }),
            items (array of strings)"""
            
            completion = client.chat.completions.create(
                model="gpt-4",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Generate a location connected to: {context}"}
                ],
                temperature=0.7
            )
            
            location_data = json.loads(completion.choices[0].message.content)
            
            # Generate image with darker theme
            image_prompt = f"dark fantasy location, {location_data['description']}, gritty, atmospheric, realistic lighting, detailed, 4k"
            image_data = await self.generate_location_image(image_prompt)
            if image_data:
                location_data["image"] = base64.b64encode(image_data).decode('utf-8')
            
            self.locations[location_data['id']] = location_data
            return location_data
            
        except Exception as e:
            logger.error(f"Error generating location: {e}")
            return None

class AdventureView(discord.ui.View):
    def __init__(self, game: AdventureGame, player: Player):
        super().__init__(timeout=180)
        self.game = game
        self.player = player
        self.history = []  # Track movement history
        self.update_buttons()
        
    def update_buttons(self):
        self.clear_items()
        location = self.game.locations.get(self.player.location)
        if not location:
            return
            
        # Movement buttons
        for path in location["paths"]:
            button = discord.ui.Button(
                label=path["name"],
                style=discord.ButtonStyle.primary,
                emoji="🚶",
                custom_id=f"move_{path['target_id']}"
            )
            
            async def button_callback(interaction: discord.Interaction, path_info=path):
                # Disable all buttons immediately
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(view=self)
                
                # Create transition embed
                loading_embed = discord.Embed(
                    title=f"🌟 {location['name']} → {path_info['name']}",
                    description=f"*{path_info['description']}*",
                    color=discord.Color.gold()
                )
                loading_embed.add_field(
                    name="Status",
                    value="```\n🚶 Moving...\n🎨 Discovering new area...\n```",
                    inline=False
                )
                
                if "image" in location:
                    image_bytes = base64.b64decode(location["image"])
                    file = discord.File(io.BytesIO(image_bytes), filename="location.png")
                    loading_embed.set_image(url="attachment://location.png")
                    await interaction.edit_original_response(embed=loading_embed, attachments=[file])
                else:
                    await interaction.edit_original_response(embed=loading_embed)
                
                # Generate new location with context from history
                if path_info["target_id"] not in self.game.locations:
                    context = self.build_location_context(path_info)
                    new_location = await self.game.generate_location(context, self.player)
                    if not new_location:
                        error_embed = discord.Embed(
                            title="❌ Error",
                            description="Failed to generate new location!",
                            color=discord.Color.red()
                        )
                        await interaction.edit_original_response(embed=error_embed)
                        return
                    
                    self.game.locations[path_info["target_id"]] = new_location
                
                # Update history and location
                self.history.append({
                    "from": self.player.location,
                    "to": path_info["target_id"],
                    "path": path_info["name"]
                })
                self.player.location = path_info["target_id"]
                await self.update_location_display(interaction)
            
            button.callback = button_callback
            self.add_item(button)
        
        # Action buttons
        if location.get("items"):
            examine_button = discord.ui.Button(
                label="Examine Area",
                style=discord.ButtonStyle.secondary,
                emoji="🔍",
                row=1
            )
            self.add_item(examine_button)
            
        inventory_button = discord.ui.Button(
            label="Inventory",
            style=discord.ButtonStyle.secondary,
            emoji="🎒",
            row=1
        )
        self.add_item(inventory_button)
        
    def build_location_context(self, path_info):
        """Build context string based on movement history"""
        current_location = self.game.locations[self.player.location]
        context_parts = []
        
        # Add current location context
        context_parts.append(f"You are leaving {current_location['name']}")
        
        # Add path context
        context_parts.append(f"through {path_info['name']}")
        
        # Add recent history context (last 2 moves)
        if self.history:
            recent = self.history[-2:]
            for move in recent:
                from_loc = self.game.locations[move['from']]
                context_parts.append(f"Previously moved from {from_loc['name']} via {move['path']}")
                
        return " | ".join(context_parts)

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
            value="\n".join([f"→ {path['name']}" for path in location["paths"]]),
            inline=False
        )
        
        if location["items"]:
            embed.add_field(
                name="👀 You Notice",
                value="\n".join([f"• {item}" for item in location["items"]]),
                inline=False
            )
        
        self.update_buttons()  # Re-enable buttons with new options
        await interaction.edit_original_response(embed=embed, view=self, attachments=files)

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
@client.tree.command(name="start_adventure", description="Start a new adventure")
async def start_adventure(interaction: discord.Interaction):
    # Create initial loading embed
    loading_embed = discord.Embed(
        title="🌟 Embarking on an Adventure",
        description="Preparing your journey into a dark realm...",
        color=discord.Color.blue()
    )
    loading_embed.add_field(
        name="Status",
        value="```\n🎨 Generating location...\n⌛ Crafting image...\n```",
        inline=False
    )
    loading_embed.set_footer(text="This may take a minute as we craft your unique experience...")
    
    await interaction.response.send_message(embed=loading_embed)
    initial_message = await interaction.original_response()
    
    # Initialize player and game
    player = Player(interaction.user.id)
    player.active_message_id = initial_message.id
    player.channel_id = interaction.channel_id
    game = AdventureGame()
    game.active_messages[initial_message.id] = player
    
    # Generate starting location
    location = await game.generate_location("starting area", player)
    if not location:
        error_embed = discord.Embed(
            title="❌ Adventure Creation Failed",
            description="Something went wrong while creating your adventure. Please try again.",
            color=discord.Color.red()
        )
        await interaction.edit_original_response(embed=error_embed)
        return
        
    player.location = location["id"]
    
    # Create the main adventure embed
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
        value="\n".join([f"→ {path['name']}" for path in location["paths"]]),
        inline=False
    )
    
    if location["items"]:
        embed.add_field(
            name="👀 You Notice",
            value="\n".join([f"• {item}" for item in location["items"]]),
            inline=False
        )
    
    embed.set_footer(text=f"Game ID: {initial_message.id} • Use /continue {initial_message.id} to resume this game")
    
    view = AdventureView(game, player)
    await interaction.edit_original_response(
        content=None,
        embed=embed,
        view=view,
        attachments=files
    )

@client.tree.command(name="continue", description="Continue an existing adventure")
@app_commands.describe(game_id="The Game ID from the adventure you want to continue")
async def continue_adventure(interaction: discord.Interaction, game_id: str):
    game = AdventureGame()
    if game_id not in game.active_messages:
        await interaction.response.send_message("Could not find an active game with that ID!", ephemeral=True)
        return
        
    player = game.active_messages[game_id]
    location = game.locations[player.location]
    
    # Create new game message in current channel
    embed = create_location_embed(location, game_id)
    view = AdventureView(game, player)
    
    await interaction.response.send_message(embed=embed, view=view)
    new_message = await interaction.original_response()
    
    # Update active message tracking
    player.active_message_id = new_message.id
    game.active_messages[new_message.id] = player

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