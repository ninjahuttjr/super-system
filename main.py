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

class Player:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.location = None
        self.health = 100
        self.is_alive = True
        self.cause_of_death = None
        self.journey_log = []
        # Add more stats
        self.rooms_explored = 0
        self.successful_choices = 0
        self.damage_taken = 0
        self.highest_risk_survived = 0

class AdventureGame:
    def __init__(self):
        self.locations = {}
        self.active_players = {}  # Track active player sessions
        
    def get_player(self, user_id: int) -> Player:
        # Check if player is already in an active session
        if user_id in self.active_players:
            return self.active_players[user_id]
            
        # Create new player
        player = Player(user_id)
        self.active_players[user_id] = player
        return player
        
    def end_game(self, user_id: int):
        """Clean up player session when game ends"""
        if user_id in self.active_players:
            del self.active_players[user_id]

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

    async def generate_location(self, context: dict, player: Player) -> Dict:
        try:
            client = OpenAI()
            
            system_prompt = """You are generating locations and choices for an adventure game.
            
            IMPORTANT: Your response must be valid JSON with EXACTLY 2 choices:
            {
                "id": "unique_location_id",
                "name": "Location Name",
                "description": "Brief, witty description",
                "paths": [
                    {
                        "name": "First Choice (keep it short)",
                        "description": "Current situation",
                        "success": "Funny success outcome + what you gained",
                        "failure": "Humorous failure outcome + what you lost",
                        "death": "How you died (make it funny but fatal)",
                        "target_id": "next_location_id",
                        "success_rate": 70,  # Higher number = easier
                        "death_rate": 10     # Chance of death on failure
                    },
                    {
                        "name": "Second Choice (keep it short)",
                        "description": "Current situation",
                        "success": "Funny success outcome + what you gained",
                        "failure": "Humorous failure outcome + what you lost",
                        "death": "How you died (make it funny but fatal)",
                        "target_id": "next_location_id",
                        "success_rate": 40,  # Lower number = harder
                        "death_rate": 25     # Higher death chance for risky choice
                    }
                ]
            }

            REQUIREMENTS:
            - EXACTLY 2 choices
            - First choice: Safer but smaller reward
            - Second choice: Riskier but bigger reward
            - Include funny death scenarios
            - Keep descriptions witty and fun
            - Success outcomes give items/rewards
            - Failure outcomes have consequences
            - Death outcomes end the game"""
            
            if not context.get('theme'):
                themes = [
                    "You're a mercenary trapped in a cyberpunk megacity",
                    "You're an escaped prisoner in a post-apocalyptic wasteland",
                    "You're a rogue special forces operator behind enemy lines",
                    "You're a street fighter rising through underground fight clubs",
                    "You're a master assassin on one last job",
                    "You're a legendary bounty hunter tracking dangerous prey",
                    "You're a cyber-enhanced hacker in a corporate war zone",
                    "You're a survivor of a zombie outbreak in a major city"
                ]
                context['theme'] = random.choice(themes)
                context['first_location'] = True
            
            character_role = context['theme'].replace("You are ", "")
            
            user_prompt = f"""Current theme: {context['theme']}
            Previous Location: {context.get('current_location', 'Starting Point')}
            Player's Last Action: {context.get('chosen_path', 'Beginning Adventure')}
            Outcome of Action: {context.get('outcome', 'Starting adventure')}
            Success/Failure: {'Succeeded' if context.get('succeeded', True) else 'Failed'}

            Generate a new location that directly acknowledges and follows from the player's previous action and its outcome.
            The description should start by mentioning what just happened, then describe the new location.
            
            Example format:
            "After your failed attempt to understand the strange device, which resulted in an angry local, you quickly retreated into... [new location description]"

            Remember to return valid JSON in the required format."""
            
            completion = client.chat.completions.create(
                model="gpt-4o-mini-2024-07-18",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.8
            )
            
            # Add debug logging
            response_content = completion.choices[0].message.content
            logger.info(f"API Response: {response_content}")
            
            # Strip markdown code block markers if present
            if response_content.startswith("```"):
                response_content = response_content.split("\n", 1)[1]
                response_content = response_content.rsplit("\n", 1)[0]
                if response_content.startswith("json"):
                    response_content = response_content[4:].lstrip()
            
            try:
                location_data = json.loads(response_content)
                location_data['theme'] = context['theme']  # Store theme with location
            except json.JSONDecodeError as e:
                logger.error(f"JSON Parse Error: {e}")
                logger.error(f"Raw Content: {response_content}")
                return None
            
            # Generate image matching the theme
            image_prompt = f"digital art, {location_data['description']}, {context['theme']}, vibrant, detailed, 4k"
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
        super().__init__(timeout=None)
        self.game = game
        self.player = player
        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        location = self.game.locations.get(self.player.location)
        if not location:
            return
            
        # Only show 2 choices
        paths = location["paths"][:2]
        
        for path in paths:
            button = discord.ui.Button(
                label=path['name'],
                style=discord.ButtonStyle.secondary,  # Default grey
                custom_id=f"path_{path['target_id']}"
            )
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        clicked_button = interaction.data['custom_id']
        
        for item in self.children:
            if item.custom_id == clicked_button:
                item.style = discord.ButtonStyle.success
                
        path_id = clicked_button.replace('path_', '')
        location = self.game.locations.get(self.player.location)
        path_info = next((p for p in location["paths"] if p["target_id"] == path_id), None)
        
        if not path_info:
            return

        # Roll for success/failure/death
        roll = random.randint(1, 100)
        if roll <= path_info['success_rate']:
            outcome = path_info['success']
            color = 0x57F287  # Green
            succeeded = True
            died = False
        else:
            # On failure, check for death
            death_roll = random.randint(1, 100)
            if death_roll <= path_info['death_rate']:
                outcome = path_info['death']
                color = 0xFF0000  # Bright red for death
                succeeded = False
                died = True
            else:
                outcome = path_info['failure']
                color = 0xED4245  # Normal red
                succeeded = False
                died = False
        
        embed = discord.Embed(
            description=f"```\n{outcome}\n```",
            color=color
        )
        
        # Show progress only if alive
        if not died:
            progress = "○ - " * 4 + "○"
            embed.add_field(name="", value=progress, inline=False)
        
        await interaction.response.edit_message(
            embed=embed,
            view=self
        )
        
        if died:
            # End the game
            game.end_game(self.player.user_id)
            
            # Add death message after delay
            await asyncio.sleep(2)
            death_embed = discord.Embed(
                title="GAME OVER",
                description="```\nYou died! Use /start to try again.\n```",
                color=0xFF0000
            )
            await interaction.edit_original_response(
                embed=death_embed,
                view=None  # Remove buttons
            )
            return
        
        # Continue game if alive
        await asyncio.sleep(2)
        
        context = {
            "current_location": location["name"],
            "chosen_path": path_info["name"],
            "succeeded": succeeded,
            "outcome": outcome
        }
        
        next_location = await self.game.generate_location(context, self.player)
        if next_location:
            self.player.location = next_location["id"]
            
            new_embed = discord.Embed(
                title=next_location['name'],
                description=f"```\n{next_location['description']}\n```",
                color=0x2f3136
            )
            
            self.update_buttons()
            
            await interaction.edit_original_response(
                embed=new_embed,
                view=self
            )

def create_error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="ERROR",
        description=f"```\n{message}\n```",
        color=discord.Color.red()
    )

# Define a custom client that supports slash commands
class MyClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        
    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name="/help"))
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

# Create instances
client = MyClient()
game = AdventureGame()

@client.tree.command(name="start", description="Begin your adventure!")
async def start_adventure(interaction: discord.Interaction):
    # Check if player is already in a game
    if interaction.user.id in game.active_players:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="ACTIVE SESSION",
                description="```\nYou must complete or die in your current adventure first.\n```",
                color=discord.Color.greyple()
            ),
            ephemeral=True
        )
        return
        
    loading_embed = discord.Embed(
        title="INITIALIZING",
        description="```\nPreparing your adventure...\n```",
        color=discord.Color.greyple()
    )
    
    await interaction.response.send_message(embed=loading_embed)
    
    player = game.get_player(interaction.user.id)
    context = {
        "current_location": "starting area",
        "chosen_path": "begin journey",
        "player_health": player.health
    }
    
    location = await game.generate_location(context, player)
    
    if not location:
        await interaction.edit_original_response(
            embed=create_error_embed("Failed to generate your adventure. Try again!")
        )
        return
        
    player.location = location["id"]
    
    embed = discord.Embed(
        title=location['name'],
        description=f"```\n{location['description']}\n```",
        color=0x2f3136
    )
    
    files = []
    if "image" in location:
        # Resize image
        image_bytes = base64.b64decode(location["image"])
        image = Image.open(io.BytesIO(image_bytes))
        
        # Calculate new size maintaining aspect ratio
        aspect_ratio = image.width / image.height
        new_height = 400
        new_width = int(new_height * aspect_ratio)
        image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        
        # Save resized image
        buffer = io.BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)
        
        file = discord.File(buffer, filename="location.png")
        embed.set_image(url="attachment://location.png")
        files.append(file)
    
    view = AdventureView(game, player)
    await interaction.edit_original_response(
        embed=embed,
        view=view,
        attachments=files
    )

# Run the client
client.run(os.getenv('DISCORD_TOKEN'))