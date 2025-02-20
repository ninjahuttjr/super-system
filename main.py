import discord
from discord.ext import commands, tasks
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
import time
from game_session_manager import GameSessionManager
from datetime import datetime, timedelta

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

# Initialize the session manager with your game instance
session_manager = GameSessionManager()

# ---- Data Classes ----

class StoryRepository:
    def __init__(self, db_path="stories.json"):
        self.db_path = db_path
        self.stories = self._load_stories()
    
    def _load_stories(self):
        try:
            with open(self.db_path, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {"themes": {}, "scenes": {}}
    
    def _save_stories(self):
        with open(self.db_path, 'w') as f:
            json.dump(self.stories, f, indent=2)
    
    def add_story(self, theme: str, scenes: List[Dict]):
        """Add a complete story branch to the repository"""
        theme_key = theme.lower().replace(" ", "_")
        if theme_key not in self.stories["themes"]:
            self.stories["themes"][theme_key] = []
        
        # Store unique scenes
        for scene in scenes:
            scene_id = scene['id']
            if scene_id not in self.stories["scenes"]:
                # Only store image if it's a success scene
                if 'image' in scene and any(
                    'success' in path and path['success'] in scene['description']
                    for path in scene['paths']
                ):
                    self.stories["scenes"][scene_id] = scene
                else:
                    # Store scene without image to save space
                    scene_copy = scene.copy()
                    scene_copy.pop('image', None)
                    self.stories["scenes"][scene_id] = scene_copy
        
        # Store the scene sequence
        story_sequence = [scene['id'] for scene in scenes]
        self.stories["themes"][theme_key].append(story_sequence)
        self._save_stories()

    def get_random_story(self, theme: str) -> Optional[List[Dict]]:
        """Get a random complete story for a theme"""
        theme_key = theme.lower().replace(" ", "_")
        if theme_key not in self.stories["themes"]:
            return None
        
        story_sequence = random.choice(self.stories["themes"][theme_key])
        return [self.stories["scenes"][scene_id] for scene_id in story_sequence]

class Item:
    def __init__(self, id: str, name: str, rarity: str, description: str, effects: dict = None):
        self.id = id
        self.name = name
        self.rarity = rarity  # common, rare, epic, legendary
        self.description = description
        self.effects = effects or {}  # {"luck": 1.1, "defense": 5, etc}

class PlayerInventory:
    def __init__(self):
        self.items = []
        self.coins = 0
        self.xp = 0
        self.level = 1
        self.titles = []
        self.stats = {
            "items_found": 0,
            "coins_earned": 0,
            "deaths": 0,
            "successful_choices": 0,
            "risky_choices_survived": 0
        }
    
    def add_item(self, item: Item):
        self.items.append(item)
        self.stats["items_found"] += 1
        
    def add_coins(self, amount: int):
        self.coins += amount
        self.stats["coins_earned"] += amount
        
    def add_xp(self, amount: int):
        self.xp += amount
        while self.xp >= self.get_next_level_xp():
            self.level_up()
            
    def level_up(self):
        self.level += 1
        
    def get_next_level_xp(self):
        return self.level * 1000  # Simple progression

class Player:
    def __init__(self):
        self.user_id = None
        self.quest_name = None
        self.main_goal = None
        self.setting = None
        self.theme_style = None
        self.total_scenes = 5  # Fixed at 5 scenes
        self.current_scene = None
        self.current_scene_number = 1  # Start at scene 1
        self.choice_history = []
        self.lives_remaining = 3
        self.max_lives = 3

# ---- Adventure Game Core ----

class AdventureGame:
    def __init__(self):
        self.MAX_SCENES = 5  # Fixed at 5 scenes
        self.active_games = {}
        self.generation_status = {}
        logger.info("AdventureGame initialized")

    def get_scaled_success_rates(self, scene_number: int) -> tuple[int, int]:
        """Returns progressively harder success rates as game progresses"""
        base_rates = {
            1: (70, 40),  # First scene: easier to encourage players
            2: (65, 35),  # Second scene: slightly harder
            3: (60, 30),  # Third scene: medium difficulty
            4: (55, 25),  # Fourth scene: challenging
            5: (50, 20)   # Final scene: most challenging
        }
        return base_rates.get(scene_number, (50, 20))  # Default to hardest if scene number invalid

    async def start_game(self, interaction: discord.Interaction) -> Player:
        """Initialize a new game session"""
        try:
            # Initialize generation status
            self.generation_status[interaction.user.id] = {
                'status': 'generating',
                'progress': 0,
                'completed_scenes': 0,
                'total_scenes': self.MAX_SCENES,
                'time_remaining': 300  # 5 minutes estimate
            }
            
            logger.info(f"Starting new game for user {interaction.user.id}")
            client = OpenAI()
            
            # Update progress after story structure generation
            self.generation_status[interaction.user.id]['progress'] = 30
            
            structure_prompt = """Create a fun, modern story for an adventure game.
            Think everyday situations with a twist, like:
            - Teaching a robot to be a food critic
            - Running tech support for time travelers
            - Tending a taco bar for aliens
            - Being an intern at a weather control station
            - Fixing bugs in a virtual reality gym
            - Running a tire shop at the border for Mexican Cartel
            
            NO fantasy clichés (no dragons, knights, fairies, unicorns, etc.)
            NO medieval or ancient settings
            
            CRITICAL: Story MUST be exactly 5 scenes long!
            
            Return ONLY JSON:
            {
                "total_scenes": 5,  # MUST be exactly 5
                "quest_name": "Short, fun title (3-4 words)",
                "main_goal": "One simple goal",
                "setting": "One modern location",
                "theme_style": "Two words for the mood (example: 'quirky tech')"
            }"""
            
            structure_response = client.chat.completions.create(
                model="gpt-4-0125-preview",
                messages=[{"role": "developer", "content": structure_prompt}],
                response_format={"type": "json_object"},
                temperature=0.9
            )
            
            structure_data = json.loads(structure_response.choices[0].message.content)
            # Force 5 scenes regardless of what was generated
            structure_data["total_scenes"] = self.MAX_SCENES
            
            logger.info(f"Generated story structure: {json.dumps(structure_data, indent=2)}")
            
            # Now generate the initial scene with knowledge of total scenes
            safe_rate, risky_rate = self.get_scaled_success_rates(1)
            
            initial_scene_prompt = f"""Generate the initial scene for this QUIRKY adventure:
            Quest Name: {structure_data['quest_name']}
            Main Goal: {structure_data['main_goal']}
            Setting: {structure_data['setting']}
            Theme Style: {structure_data['theme_style']}
            
            CRITICAL REQUIREMENTS:
            1. Scene must be ONE clear, punchy sentence (max 20 words)
            2. Focus on ONE specific problem or obstacle
            3. Choices must be clear, specific actions (2-3 words)

            Examples of GOOD scenes:
            - "The ghost demands 1000 followers by midnight, but your phone is possessed by a social media influencer."
            - "The dragon keeps putting customers on hold to eat their complaints forms."
            
            Examples of BAD scenes:
            - "Time portals are everywhere and customers are confused and there's also a problem with the coffee machine..."
            - "You find yourself in a magical office where nothing makes sense and everything is chaotic..."

            Return ONLY JSON:
            {{
                "description": "ONE clear, focused sentence",
                "choices": [
                    {{"text": "Clear Action Choice", "success_rate": {safe_rate}}},
                    {{"text": "Clear Action Choice", "success_rate": {risky_rate}}}
                ]
            }}"""

            initial_scene_response = client.chat.completions.create(
                model="gpt-4o-2024-08-06",
                messages=[{"role": "developer", "content": initial_scene_prompt}],
                response_format={"type": "json_object"},
                temperature=0.9
            )
            
            scene_data = json.loads(initial_scene_response.choices[0].message.content)
            logger.info(f"Generated initial scene: {json.dumps(scene_data, indent=2)}")
            
            player = Player()
            player.user_id = interaction.user.id
            player.quest_name = structure_data["quest_name"]
            player.main_goal = structure_data["main_goal"]
            player.setting = structure_data["setting"]
            player.theme_style = structure_data["theme_style"]
            player.total_scenes = structure_data["total_scenes"]
            player.current_scene = scene_data
            
            self.active_games[interaction.user.id] = player
            
            # Update status when complete
            self.generation_status[interaction.user.id] = {
                'status': 'complete',
                'progress': 100,
                'completed_scenes': self.MAX_SCENES,
                'total_scenes': self.MAX_SCENES,
                'time_remaining': 0
            }
            
            return player
            
        except Exception as e:
            # Update status on error
            if interaction.user.id in self.generation_status:
                self.generation_status[interaction.user.id]['status'] = 'error'
            logger.error(f"Error in start_game: {str(e)}")
            raise

    async def create_game_embed(self, player: Player) -> discord.Embed:
        """Create embed with scene description and lives"""
        embed = discord.Embed(
            title=player.quest_name,
            description=player.current_scene['description'],
            color=0x2f3136
        )
        
        # Progress bar using custom emojis or unicode
        progress = "○" * player.total_scenes
        current_progress = min(player.current_scene_number - 1, player.total_scenes)  # Use current_scene_number
        progress = progress[:current_progress] + "●" + progress[current_progress + 1:]
        
        # Add lives display with heart emojis
        lives = "❤️" * player.lives_remaining + "🖤" * (player.max_lives - player.lives_remaining)
        
        embed.add_field(
            name="📋 Objective",
            value=player.main_goal,
            inline=False
        )
        
        embed.set_footer(text=f"{progress} | Scene {player.current_scene_number}/{player.total_scenes} | Lives: {lives}")
        return embed

    async def generate_next_scene(self, player: Player, previous_choice: str, success: bool, failure_message: str = None) -> Dict:
        """Generate next scene that follows from previous events"""
        # Get appropriate success rates for the next scene
        safe_rate, risky_rate = self.get_scaled_success_rates(player.current_scene_number + 1)
        
        # Build story context from recent history
        story_context = "Recent events:\n"
        for choice in player.choice_history[-2:]:  # Last 2 choices
            story_context += f"- Scene: {choice['description']}\n"
            story_context += f"  Player chose: {choice['choice']}\n"
        
        # Add the failure message if it exists
        if not success and failure_message:
            story_context += f"Result: {failure_message}\n"
        
        scene_prompt = f"""Create the next scene that follows from:
        Quest: {player.quest_name}
        Goal: {player.main_goal}
        Setting: {player.setting}
        Style: {player.theme_style}
        
        {story_context}
        Last Choice: {previous_choice}
        Was Successful: {success}
        
        Rules:
        1. Keep it modern and relatable
        2. No fantasy clichés
        3. MUST directly reference or continue from the last choice AND failure message if it exists
        4. Use simple, clear language
        5. One choice should be normal, one should be wild
        6. CRITICAL: Choice text must be 80 characters or less!
        
        Return ONLY JSON:
        {{
            "description": "What happens BECAUSE OF their last choice? (2 sentences max)",
            "quest_status": "Simple progress update",
            "choices": [
                {{"text": "Short, clear choice (max 80 chars)", "success_rate": {safe_rate}}},
                {{"text": "Wild alternative (max 80 chars)", "success_rate": {risky_rate}}}
            ]
        }}"""

        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4-0125-preview",
            messages=[{"role": "developer", "content": scene_prompt}],
            response_format={"type": "json_object"},
            temperature=0.7
        )
        
        scene_data = json.loads(response.choices[0].message.content)
        logger.info(f"Generated scene:\n{json.dumps(scene_data, indent=2)}")
        return scene_data

    async def generate_failure_message(self, player: Player, failed_choice: str, roll: int, needed: int) -> Dict:
        """Generate a simple failure message"""
        prompt = f"""Write a SHORT, funny failure message.
        Scene: {player.current_scene['description']}
        Failed Action: {failed_choice}
        Roll: {roll} (needed {needed} or less)
        
        Rules:
        1. Keep it short (2 sentences max)
        2. Use simple words
        3. Make it funny but clear
        4. No fancy language
        
        Return ONLY JSON:
        {{
            "message": "Short, funny failure message"
        }}"""

        logger.info(f"Generating failure message with context:\n{prompt}")
        
        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-2024-08-06",
            messages=[{
                "role": "developer",
                "content": prompt
            }],
            response_format={"type": "json_object"},
            temperature=0.7
        )
        
        failure_data = json.loads(response.choices[0].message.content)
        logger.info(f"Generated failure message:\n{json.dumps(failure_data, indent=2)}")
        return failure_data

    async def generate_victory_scene(self, player: Player, final_choice: str) -> Dict:
        """Generate a victory ending"""
        victory_prompt = f"""Write a simple victory ending for:
        Quest: {player.quest_name}
        Goal: {player.main_goal}
        Setting: {player.setting}
        Final Action: {final_choice}
        
        Rules:
        1. Keep it short and sweet
        2. Use simple words
        3. Make it feel like a win
        4. No fancy language
        
        Return ONLY JSON:
        {{
            "description": "How did they win? (2-3 short sentences)",
            "quest_status": "Simple victory message",
            "is_victory": true
        }}"""

        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4-0125-preview",
            messages=[{"role": "developer", "content": victory_prompt}],
            response_format={"type": "json_object"},
            temperature=0.7
        )
        
        return json.loads(response.choices[0].message.content)

    async def generate_processing_message(self, theme: str, setting: str, choice: str) -> Dict:
        """Generate a simple waiting message"""
        prompt = f"""Create a SHORT waiting message that fits:
        Style: {theme}
        Setting: {setting}
        Choice: {choice}
        
        Rules:
        1. One short sentence only
        2. Use simple words
        3. Keep it light and fun
        4. No fancy language
        
        Return ONLY JSON:
        {{
            "processing_message": "One short, fun sentence",
            "result_title": "2-3 simple words"
        }}"""

        client = OpenAI()
        response = client.chat.completions.create(
            model="gpt-4o-2024-08-06",
            messages=[{
                "role": "developer",
                "content": prompt
            }],
            response_format={"type": "json_object"},
            temperature=0.7
        )
        
        processing_data = json.loads(response.choices[0].message.content)
        logger.info(f"Generated processing message:\n{json.dumps(processing_data, indent=2)}")
        return processing_data

    async def handle_game_over(self, interaction: discord.Interaction, player: Player, failure_message: str):
        """Handle game over state"""
        try:
            game_over_embed = discord.Embed(
                title="💀 Game Over",
                description=failure_message,
                color=0xff0000
            )
            
            game_over_embed.add_field(
                name="Final Report",
                value=f"Scenes Completed: {player.current_scene_number}/5\n"  # Fixed at 5
                      f"Lives Used: {player.max_lives - player.lives_remaining}/{player.max_lives}\n"
                      f"Final Scene: {player.current_scene['description']}",
                inline=False
            )
            
            await interaction.edit_original_response(
                embed=game_over_embed,
                view=None
            )
            
            # Clean up game state
            if interaction.user.id in self.active_games:
                del self.active_games[interaction.user.id]
            if interaction.user.id in self.generation_status:
                del self.generation_status[interaction.user.id]
            
            # End the session
            session_manager.end_session(interaction.user.id)
            
        except Exception as e:
            logger.error(f"Error in handle_game_over: {e}")
            # Ensure cleanup even on error
            if interaction.user.id in self.active_games:
                del self.active_games[interaction.user.id]
            if interaction.user.id in self.generation_status:
                del self.generation_status[interaction.user.id]
            session_manager.end_session(interaction.user.id)

    async def process_choice(self, interaction: discord.Interaction, choice_text: str, success_rate: int):
        """Process player choice and determine outcome"""
        player = self.active_games[interaction.user.id]
        
        # Roll for success
        roll = random.randint(1, 100)
        success = roll <= success_rate
        
        # Record choice at CURRENT scene number
        player.choice_history.append({
            'scene': player.current_scene_number,
            'choice': choice_text,
            'description': player.current_scene["description"]
        })
        
        # Handle failure
        if not success:
            player.lives_remaining -= 1
            if player.lives_remaining <= 0:
                await self.handle_game_over(interaction, player)
                return
            
            failure_data = await self.generate_failure_message(player, choice_text, roll, success_rate)
            
            # Show failure message
            failure_embed = discord.Embed(
                title="💔 Life Lost!",
                description=failure_data["message"],
                color=0xff7700
            )
            failure_embed.add_field(
                name="Consequence",
                value=f"Lives Remaining: {'❤️' * player.lives_remaining}\nDealing with the aftermath...",
                inline=False
            )
            await interaction.edit_original_response(embed=failure_embed, view=None)
            await asyncio.sleep(4)
        
        else:
            # Success path
            success_embed = discord.Embed(
                title="✅ Success!",
                description=f"Your roll of {roll} was enough! Moving forward...",
                color=0x00ff00
            )
            await interaction.edit_original_response(embed=success_embed, view=None)
            await asyncio.sleep(2)
        
        # Generate next scene
        next_scene = await self.generate_next_scene(
            player,
            choice_text,
            success
        )
        player.current_scene = next_scene
        
        # Increment scene number ONCE after processing either success or failure
        player.current_scene_number += 1
        
        # Show new scene
        new_embed = await self.create_game_embed(player)
        await interaction.edit_original_response(
            embed=new_embed,
            view=AdventureView(self, player)
        )

    async def handle_victory(self, interaction: discord.Interaction, player: Player, final_choice: str):
        """Handle victory state"""
        try:
            victory_scene = await self.generate_victory_scene(player, final_choice)
            
            # Create victory embed
            embed = discord.Embed(
                title="🎉 Quest Complete: " + player.quest_name,
                description=victory_scene["description"],
                color=0x00ff00  # Green for victory
            )
            
            embed.add_field(
                name="Final Status",
                value=victory_scene["quest_status"],
                inline=False
            )
            
            embed.add_field(
                name="🏆 Reward",
                value=victory_scene["reward_description"],
                inline=False
            )
            
            # Add statistics
            embed.add_field(
                name="📊 Adventure Summary",
                value=f"Scenes Completed: {self.MAX_SCENES}/{self.MAX_SCENES}\n"
                      f"Lives Remaining: {player.lives_remaining}/{player.max_lives}\n"
                      f"Final Action: {final_choice}",
                inline=False
            )
            
            await interaction.edit_original_response(
                embed=embed,
                view=None
            )
            
            # Clean up game state
            if interaction.user.id in self.active_games:
                del self.active_games[interaction.user.id]
            if interaction.user.id in self.generation_status:
                del self.generation_status[interaction.user.id]
            
        except Exception as e:
            logger.error(f"Error in handle_victory: {e}")
            # Still try to clean up game state on error
            if interaction.user.id in self.active_games:
                del self.active_games[interaction.user.id]
            if interaction.user.id in self.generation_status:
                del self.generation_status[interaction.user.id]

# ---- Discord UI and Bot Commands ----

class AdventureView(discord.ui.View):
    def __init__(self, game: AdventureGame, player: Player):
        super().__init__()
        self.game = game
        self.player = player
        
        # Only add choice buttons if not a victory scene
        if not player.current_scene.get("is_victory", False):
            for i, choice in enumerate(player.current_scene["choices"]):
                self.add_item(ChoiceButton(i, choice["text"], choice["success_rate"]))

class ChoiceButton(discord.ui.Button):
    def __init__(self, index: int, label: str, success_rate: int):
        super().__init__(style=discord.ButtonStyle.primary, label=label)
        self.success_rate = success_rate

    async def callback(self, interaction: discord.Interaction):
        try:
            # Validate the interaction through session manager
            should_process, message = await session_manager.handle_interaction(interaction)
            if not should_process:
                await interaction.response.send_message(message, ephemeral=True)
                return

            view: AdventureView = self.view
            if interaction.user.id != view.player.user_id:
                await interaction.response.send_message("This isn't your adventure!", ephemeral=True)
                return

            # Disable all buttons immediately
            for item in view.children:
                item.disabled = True
                if item == self:
                    item.style = discord.ButtonStyle.success
                else:
                    item.style = discord.ButtonStyle.secondary
            
            await interaction.response.edit_message(view=view)
            
            # Generate suspense message
            processing_data = await view.game.generate_processing_message(
                view.player.theme_style,
                view.player.setting,
                self.label
            )
            
            # Show choice processing
            suspense_embed = discord.Embed(
                title="⏳ " + processing_data["processing_message"],
                description=f"You chose: {self.label}",
                color=0xffff00
            )
            await interaction.edit_original_response(embed=suspense_embed, view=view)
            await asyncio.sleep(3)
            
            # Roll for success
            roll = random.randint(1, 100)
            success = roll <= self.success_rate
            
            # Show roll result
            result_embed = discord.Embed(
                title=processing_data["result_title"],
                description=f"Required: {self.success_rate} or less\nActual: {roll}",
                color=0xffff00
            )
            await interaction.edit_original_response(embed=result_embed, view=view)
            await asyncio.sleep(2)

            # Record the choice
            view.player.choice_history.append({
                'scene': view.player.current_scene_number,
                'choice': self.label,
                'description': view.player.current_scene["description"]
            })

            # Check if this is the final scene
            is_final_scene = view.player.current_scene_number >= 5  # Explicit check against 5

            if not success:
                view.player.lives_remaining -= 1
                failure_data = await view.game.generate_failure_message(
                    view.player,
                    self.label,
                    roll,
                    self.success_rate
                )
                
                if view.player.lives_remaining <= 0 or (is_final_scene and not success):
                    # Game Over - Either no lives left or failed final scene
                    await view.game.handle_game_over(interaction, view.player, failure_data["message"])
                    return

            # Handle victory if succeeded on final scene
            if is_final_scene and success:
                await view.game.handle_victory(interaction, view.player, self.label)
                return

            # Only generate next scene if not final scene
            if not is_final_scene:
                # Generate next scene
                next_scene = await view.game.generate_next_scene(
                    view.player,
                    self.label,
                    success,
                    failure_data["message"] if not success else None
                )
                
                # Increment scene number
                view.player.current_scene_number += 1
                
                # Continue to next scene
                view.player.current_scene = next_scene
                new_embed = await view.game.create_game_embed(view.player)
                await interaction.edit_original_response(
                    embed=new_embed,
                    view=AdventureView(view.game, view.player)
                )

        except Exception as e:
            logger.error(f"Error in button callback: {str(e)}")
            # Ensure cleanup on error
            if view.game and interaction.user.id in view.game.active_games:
                del view.game.active_games[interaction.user.id]
            session_manager.end_session(interaction.user.id)

def create_error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="ERROR",
        description=f"```\n{message}\n```",
        color=discord.Color.red()
    )

def create_location_embed(location: Dict, player: Player) -> discord.Embed:
    embed = discord.Embed(
        title=f"🎯 {location['name']}", 
        description=f"```\n{location['description']}\n```",
        color=0x2f3136
    )
    embed.set_footer(text=f"Quest: Recover the Forgotten Relic | Scene {player.current_scene_number}")
    embed.add_field(
        name="📊 Stats",
        value=f"XP: {player.inventory.xp}/{player.inventory.get_next_level_xp()}\nCoins: {player.inventory.coins}",
        inline=True
    )
    return embed

# ---- Discord Bot Client ----

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

        # Start the periodic session cleanup task
        cleanup_sessions.start()

client = MyClient()
game = AdventureGame()

@client.tree.command(name="start", description="Start a new adventure")
async def start(interaction: discord.Interaction):
    try:
        # Create a session first
        success, message = session_manager.create_session(interaction.user.id, interaction.channel_id)
        if not success:
            await interaction.response.send_message(message, ephemeral=True)
            return

        # Send immediate response
        initial_embed = discord.Embed(
            title="Generating Your Adventure",
            description="```Crafting a unique quest just for you...```",
            color=0x2b2d31  # Discord's dark theme gray, matches the UI
        )
        initial_embed.add_field(
            name="```Please Wait```",
            value="```Your adventure is being prepared. This may take a few seconds.```",
            inline=False
        )
        response = await interaction.response.send_message(embed=initial_embed)
        
        # Generate the game in the background
        player = await game.start_game(interaction)
        game_embed = await game.create_game_embed(player)
        
        # Update the message with the actual game content
        message = await interaction.edit_original_response(
            embed=game_embed,
            view=AdventureView(game, player)
        )
        
        # Register the message ID with the session manager
        session_manager.register_message(interaction.user.id, message.id)
        
    except Exception as e:
        session_manager.end_session(interaction.user.id)
        logger.error(f"Error in start command: {e}")
        error_embed = discord.Embed(
            title="❌ Error",
            description="An error occurred while generating your adventure. Please try again.",
            color=0xff0000  # Red for error state
        )
        # If we haven't responded yet, send new message
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=error_embed)
        else:
            # If we already responded, edit the existing message
            await interaction.edit_original_response(embed=error_embed)

@client.tree.command(name="status", description="Check the status of your adventure generation")
async def status(interaction: discord.Interaction):
    user_id = interaction.user.id
    
    if user_id not in game.generation_status:
        await interaction.response.send_message(
            content="You don't have any adventures being generated. Use /start to begin!",
            ephemeral=True
        )
        return

    status = game.generation_status[user_id]
    if status['status'] == 'generating':
        minutes_remaining = int(status.get('time_remaining', 0) / 60)
        await interaction.response.send_message(
            content=f"🎮 Your adventure is being prepared!\n"
                   f"Progress: {status['progress']:.1f}%\n"
                   f"Scenes completed: {status['completed_scenes']}/{status['total_scenes']}\n"
                   f"Estimated time remaining: {minutes_remaining} minutes",
            ephemeral=True
        )
    elif status['status'] == 'complete':
        await interaction.response.send_message(
            content="✅ Your adventure is ready! Use /start to begin playing!",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            content="❌ There was an error generating your adventure. Please try /start again.",
            ephemeral=True
        )

@client.tree.command(name="inventory", description="View your inventory and stats!")
async def inventory(interaction: discord.Interaction):
    player = game.get_player(interaction.user.id)
    
    embed = discord.Embed(
        title=f"🎒 {interaction.user.name}'s Inventory",
        color=0x2f3136
    )
    
    embed.add_field(
        name="📊 Stats",
        value=f"Level: {player.inventory.level}\nXP: {player.inventory.xp}/{player.inventory.get_next_level_xp()}\nCoins: {player.inventory.coins}",
        inline=False
    )
    
    items_text = ""
    for item in player.inventory.items:
        effects = ", ".join(f"{k}: {v}" for k, v in item.effects.items())
        items_text += f"• {item.name} ({item.rarity})\n  {item.description}\n  Effects: {effects}\n"
    
    embed.add_field(
        name="🗃️ Items",
        value=items_text or "No items yet!",
        inline=False
    )
    
    embed.add_field(
        name="🏆 Achievements",
        value=f"Items Found: {player.inventory.stats['items_found']}\nCoins Earned: {player.inventory.stats['coins_earned']}\nSuccessful Choices: {player.inventory.stats['successful_choices']}\nRisky Choices Survived: {player.inventory.stats['risky_choices_survived']}",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

class RewardManager:
    def __init__(self):
        self.common_rewards = {
            "items": [
                Item("health_potion", "Health Potion", "common", "Restores 25 HP", {"healing": 25}),
                Item("basic_shield", "Basic Shield", "common", "Reduces damage by 10%", {"defense": 1.1}),
                Item("lucky_coin", "Lucky Coin", "common", "Slightly increases success chance", {"luck": 1.05})
            ],
            "coin_range": (100, 500)
        }
        
        self.rare_rewards = {
            "items": [
                Item("energy_shield", "Energy Shield", "rare", "Reduces damage by 25%", {"defense": 1.25}),
                Item("hackers_toolkit", "Hacker's Toolkit", "rare", "Increases success on tech choices", {"tech_skill": 1.2}),
                Item("stealth_suit", "Stealth Suit", "rare", "Better chances on stealth actions", {"stealth": 1.15})
            ],
            "coin_range": (500, 2000)
        }
        
        self.epic_rewards = {
            "items": [
                Item("quantum_device", "Quantum Device", "epic", "Major boost to all success rates", {"luck": 1.3}),
                Item("legendary_weapon", "Plasma Rifle", "epic", "Massive advantage in combat", {"combat": 1.4})
            ],
            "coin_range": (2000, 5000)
        }
    
    def generate_reward(self, risk_level: int) -> tuple[Item, int]:
        if risk_level > 80:
            pool = self.epic_rewards
        elif risk_level > 50:
            pool = self.rare_rewards
        else:
            pool = self.common_rewards
        item = random.choice(pool["items"])
        coins = random.randint(*pool["coin_range"])
        return item, coins

reward_manager = RewardManager()

@tasks.loop(minutes=5)
async def cleanup_sessions():
    try:
        expired = await session_manager.check_sessions(client)
        if expired:
            logger.info(f"Cleaned up {len(expired)} expired game sessions")
            
            # Clean up game states for expired sessions
            for user_id in expired:
                if user_id in game.active_games:
                    del game.active_games[user_id]
                if user_id in game.generation_status:
                    del game.generation_status[user_id]
                    
    except Exception as e:
        logger.error(f"Error in cleanup_sessions: {e}")

@client.tree.command(name="session", description="Check your current game session status")
async def session_status(interaction: discord.Interaction):
    """Check the status of your current game session"""
    try:
        session = session_manager.get_session(interaction.user.id)
        if not session:
            await interaction.response.send_message(
                "You don't have an active game session.",
                ephemeral=True
            )
            return
        
        # Calculate time remaining
        time_since_interaction = datetime.now() - session.last_interaction
        time_remaining = timedelta(minutes=session_manager.timeout_minutes) - time_since_interaction
        
        embed = discord.Embed(
            title="Game Session Status",
            color=0x2f3136
        )
        
        embed.add_field(
            name="Status",
            value=session.state.value.title(),
            inline=True
        )
        
        embed.add_field(
            name="Time Remaining",
            value=f"{int(time_remaining.total_seconds() / 60)} minutes",
            inline=True
        )
        
        if interaction.user.id in game.active_games:
            player = game.active_games[interaction.user.id]
            embed.add_field(
                name="Current Game",
                value=f"Quest: {player.quest_name}\nScene: {player.current_scene_number}/{game.MAX_SCENES}\nLives: {player.lives_remaining}/{player.max_lives}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    except Exception as e:
        logger.error(f"Error in session_status command: {e}")
        await interaction.response.send_message(
            "An error occurred while checking your session status.",
            ephemeral=True
        )

@client.tree.command(name="end", description="End your current game session")
@app_commands.guild_only()  # Optional: restrict to guilds only
async def end(interaction: discord.Interaction):
    """End the current game session"""
    try:
        session = session_manager.get_session(interaction.user.id)
        if not session:
            await interaction.response.send_message(
                "You don't have an active game session.",
                ephemeral=True
            )
            return
        
        # End the session
        session_manager.end_session(interaction.user.id)
        
        # Clean up game state if it exists
        if interaction.user.id in game.active_games:
            del game.active_games[interaction.user.id]
        if interaction.user.id in game.generation_status:
            del game.generation_status[interaction.user.id]
        
        await interaction.response.send_message(
            "Your game session has been ended. Use `/start` to begin a new adventure!",
            ephemeral=True
        )
        
    except Exception as e:
        logger.error(f"Error in end command: {e}")
        await interaction.response.send_message(
            "An error occurred while ending your session.",
            ephemeral=True
        )

# Make sure to sync commands on startup
@client.event
async def on_ready():
    try:
        await client.tree.sync()
        logger.info(f'Logged in as {client.user} (ID: {client.user.id})')
        logger.info('------')
    except Exception as e:
        logger.error(f"Error syncing commands: {e}")

client.run(os.getenv('DISCORD_TOKEN'))
