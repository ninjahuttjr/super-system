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
import uuid

# Load environment variables
load_dotenv()

# Setup OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

# Set up logging
logger = logging.getLogger('AdventureGame')
logger.setLevel(logging.DEBUG)  # Change to DEBUG for more detail
handler = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(name)s: %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# Initialize the session manager with your game instance
session_manager = GameSessionManager()

# At the top level of your script
# client = MyClient()
# game = AdventureGame()
# openai_client = OpenAI()  # Initialize once

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
    """Player object for storing game state"""
    def __init__(self):
        self.user_id = None
        self.quest_name = ""
        self.main_goal = ""
        self.setting = ""
        self.theme_style = ""
        self.current_scene = {}
        self.current_scene_number = 1
        self.total_scenes = 5
        self.choice_history = []  # Make sure this is initialized
        self.lives_remaining = 3
        self.max_lives = 3
        self.inventory = PlayerInventory()

# ---- Adventure Game Core ----

class AdventureGame:
    """Main game logic for the Adventure Bot"""
    
    def __init__(self):
        """Initialize the game state"""
        self.MAX_SCENES = 5  # Keep the standard 5 scenes
        self.active_games = {}
        self.roll_history = {}
        self.generation_status = {}
        
        # Maximum concurrent games
        self.MAX_CONCURRENT_GAMES = 5
        
        logger.info("AdventureGame initialized")
    
    def get_scaled_success_rates(self, scene_number: int) -> tuple[int, int]:
        """Returns progressively harder success rates as game progresses"""
        # Make the game slightly easier with higher success rates
        base_rates = {
            1: (75, 45),  # First scene: very easy to encourage players
            2: (70, 40),  # Second scene: still relatively easy
            3: (65, 35),  # Third scene: medium difficulty
            4: (60, 30),  # Fourth scene: challenging
            5: (55, 25)   # Final scene: most challenging, but still doable
        }
        return base_rates.get(scene_number, (55, 25))  # Default to hardest if scene number invalid

    async def start_game(self, interaction: discord.Interaction) -> Player:
        """Initialize a new game session"""
        try:
            logger.info(f"=== STARTING NEW GAME ===")
            logger.info(f"Player: {interaction.user.name} (ID: {interaction.user.id})")
            
            # Log generation status
            logger.debug(f"Setting initial generation status")
            self.generation_status[interaction.user.id] = {
                'status': 'generating',
                'progress': 0,
                'completed_scenes': 0,
                'total_scenes': self.MAX_SCENES,
                'time_remaining': 300
            }
            
            # Log story generation
            logger.info("Generating story structure...")
            structure_response = await self.generate_story_structure()
            logger.info(f"Story Structure: {json.dumps(structure_response, indent=2)}")
            
            # Log initial scene generation
            logger.info("Generating initial scene...")
            initial_scene = await self.generate_initial_scene(structure_response)
            logger.info(f"Initial Scene: {json.dumps(initial_scene, indent=2)}")
            
            # Log player creation
            logger.info("Creating new player object...")
            player = Player()
            player.user_id = interaction.user.id
            player.quest_name = structure_response["quest_name"]
            player.main_goal = structure_response["main_goal"]
            player.setting = structure_response["setting"]
            player.theme_style = structure_response["theme_style"]
            player.current_scene = initial_scene
            
            logger.debug(f"New Player Object: {vars(player)}")
            
            # Log game state storage
            logger.info("Storing game state...")
            self.active_games[interaction.user.id] = player
            
            return player
            
        except Exception as e:
            logger.error(f"Error in start_game: {str(e)}", exc_info=True)
            raise

    async def create_game_embed(self, player: Player) -> discord.Embed:
        """Create the game embed with scene info"""
        # Create a more visually appealing embed with consistent colors
        embed = discord.Embed(
            title=f"```{player.quest_name}```",
            description=f"**Scene {player.current_scene_number}/{player.total_scenes}:** {player.current_scene['description']}",
            color=COLORS["PRIMARY"]
        )
        
        # Add footer with more info about the world/setting
        embed.set_footer(text=f"Lives: {'❤️' * player.lives_remaining}{'🖤' * (player.max_lives - player.lives_remaining)}")
        
        return embed

    async def generate_next_scene(self, player: Player, previous_choice: str, success: bool, failure_message: str = None) -> Dict:
        """Generate next scene that follows from previous events"""
        safe_rate, risky_rate = self.get_scaled_success_rates(player.current_scene_number + 1)
        
        # Build a choice history context for better continuity
        choice_context = "Previous choices:\n"
        if player.choice_history:
            for i, choice in enumerate(player.choice_history[-3:]):  # Last 3 choices for context
                choice_context += f"- Scene {choice['scene']}: {choice['choice']} ({choice['outcome']})\n"
        else:
            choice_context += "This is the first choice in your adventure.\n"
        
        scene_prompt = f"""Create the next scene for:
        Quest: {player.quest_name}
        Main Goal: {player.main_goal}
        Setting: {player.setting}
        Previous Choice: {previous_choice}
        Success: {success}
        Current Scene: {player.current_scene_number + 1}/{player.total_scenes}
        {choice_context}

        CRITICAL RULES:
        1. Description MUST be ONE SHORT, DRY, WITTY sentence
        2. Think Douglas Adams meets Portal's GLaDOS
        3. NO flowery language or long descriptions
        4. Choices must be under 80 chars and clever
        5. IMPORTANT: ALL choices and descriptions MUST relate to {player.quest_name}
        6. IMPORTANT: EVERY scene MUST advance the story toward {player.main_goal}
        7. STICK TO THE THEME - no random new elements that weren't established
        
        Examples of GOOD descriptions:
        - "The quantum AI has decided to become a stand-up comedian, and nobody has the heart to tell it it's not funny."
        - "Turns out uploading consciousness to the cloud wasn't great for data storage costs."
        - "The memes have unionized and are demanding better working conditions."
        
        Examples of BAD descriptions:
        - Anything longer than one sentence
        - Flowery or dramatic language
        - Generic fantasy/sci-fi descriptions
        - ANYTHING that doesn't directly relate to the established quest theme
        
        Return ONLY JSON:
        {{
            "description": "ONE short, witty sentence",
            "choices": [
                {{"text": "Clever choice (max 80 chars)", "success_rate": {safe_rate}}},
                {{"text": "Witty risky choice (max 80 chars)", "success_rate": {risky_rate}}}
            ]
        }}"""

        try:
            logger.info(f"=== GENERATING SCENE {player.current_scene_number + 1} ===")
            logger.info(f"Previous choice: {previous_choice}")
            logger.info(f"Success: {success}")
            logger.info(f"Failure message: {failure_message}")
            logger.info(f"Scene prompt:\n{scene_prompt}")
            
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": scene_prompt}],
                response_format={"type": "json_object"},
                temperature=0.8
            )
            
            scene_data = json.loads(response.choices[0].message.content)
            logger.info(f"Generated scene data: {json.dumps(scene_data, indent=2)}")
            
            # Validate choice lengths
            for choice in scene_data["choices"]:
                if len(choice["text"]) > 80:
                    logger.warning(f"Choice too long, truncating: {choice['text']}")
                    choice["text"] = choice["text"][:77] + "..."
            
            return scene_data

        except Exception as e:
            logger.error(f"Error generating scene: {str(e)}", exc_info=True)
            return {
                "description": "The universe blue-screened. No pressure.",
                "choices": [
                    {"text": "Try turning it off and on again", "success_rate": safe_rate},
                    {"text": "Hack the mainframe", "success_rate": risky_rate}
                ]
            }

    async def generate_failure_message(self, player: Player, choice_text: str, roll: int, required: int) -> Dict:
        """Generate a contextual failure message"""
        prompt = f"""Write a SHORT, contextual failure message.
            Scene: {player.current_scene['description']}
            Failed Action: {choice_text}
            Roll: {roll} (needed {required} or less)

            CRITICAL RULES:
            1. Keep it short (1-2 sentences)
            2. Message MUST directly relate to the scene and action
            3. Maintain the serious sci-fi/tech tone
            4. NO random elements unrelated to the scene
            5. NO silly memes or internet references

            Return ONLY JSON:
            {{
                "message": "Short, contextual failure message"
            }}"""

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.7
            )
            
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Error generating failure message: {e}")
            return {"message": "The attempt failed. Try a different approach."}

    async def generate_victory_scene(self, player: Player, final_choice: str, success: bool) -> Dict:
        """Generate a victory scene based on the player's journey"""
        
        # Build a more detailed narrative of the player's journey
        choice_narrative = "Player's journey:\n"
        for choice in player.choice_history:
            choice_narrative += f"- Scene {choice['scene']}: {choice['choice']} ({choice['outcome']})\n"
        
        # Include information about lives lost
        lives_lost = player.max_lives - player.lives_remaining
        life_status = f"You completed this adventure with {player.lives_remaining} lives remaining."
        if lives_lost > 0:
            life_status += f" You faced {lives_lost} major setback(s) along the way."
        
        prompt = f"""Create a victory scene for:
        Quest: {player.quest_name}
        Main Goal: {player.main_goal}
        Setting: {player.setting}
        Theme: {player.theme_style}
        Final Choice: {final_choice}
        Success: {success}
        Lives Remaining: {player.lives_remaining}/{player.max_lives}
        
        {choice_narrative}
        
        Create a self-aware, witty conclusion that references:
        1. The player's specific choices throughout their journey
        2. Any failures or setbacks they encountered
        3. The main quest objective and how it was resolved
        4. Be Douglas Adams meets Portal's GLaDOS in tone (dry humor)
        
        Return ONLY JSON:
        {{
            "title": "A clever, punchy victory title",
            "description": "2-3 sentences describing the victory that references specific player choices",
            "quest_status": "One line final status with dry humor",
            "reward": "Unique reward that fits the story and player's journey",
            "epilogue": "A single funny line about what happens after the adventure"
        }}"""
        
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.7
            )
            
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Error generating victory scene: {e}")
            return {
                "title": "Mission Accomplished... Probably",
                "description": "You completed your quest successfully, though the universe seems mildly surprised.",
                "quest_status": "Victory, though nobody's quite sure how you managed it!",
                "reward": "A trophy of achievement that occasionally questions your methods",
                "epilogue": "And so the adventure ends, until the next laundry day..."
            }

    async def generate_processing_message(self, choice: str) -> dict:
        """Generate a processing message"""
        # Use simpler, fixed processing messages instead of generating them
        processing_messages = [
            {"processing_message": "Working on it...", "result_title": "Processing"},
            {"processing_message": "The story continues...", "result_title": "Next Chapter"},
            {"processing_message": "Calculating consequences...", "result_title": "Thinking"},
            {"processing_message": "Rewriting reality...", "result_title": "Please Wait"},
            {"processing_message": "Consulting the void...", "result_title": "Loading"},
            {"processing_message": "Spinning up new possibilities...", "result_title": "Creating"}
        ]
        
        # Choose a random processing message
        return random.choice(processing_messages)

    async def handle_game_over(self, interaction: discord.Interaction, player: Player, failure_message: str):
        """Handle game over state"""
        try:
            # Log roll history before cleanup
            if interaction.user.id in self.roll_history:
                logger.info(f"Final roll history for user {interaction.user.id}:")
                for roll_data in self.roll_history[interaction.user.id]:
                    logger.info(f"  Scene {roll_data['scene']}: Roll {roll_data['roll']} (needed {roll_data['required']}) - {roll_data['choice']}")
            
            game_over_embed = discord.Embed(
                title="💀 Game Over",
                description=failure_message,
                color=0xff0000
            )
            
            # Add roll history to embed
            if interaction.user.id in self.roll_history:
                rolls_text = "\n".join([
                    f"Scene {r['scene']}: {r['roll']} vs {r['required']} - {r['choice']}"
                    for r in self.roll_history[interaction.user.id]
                ])
                game_over_embed.add_field(
                    name="Roll History",
                    value=f"```\n{rolls_text}\n```",
                    inline=False
                )
            
            game_over_embed.add_field(
                name="Final Report",
                value=f"Scenes Completed: {player.current_scene_number}/{self.MAX_SCENES}\n"
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
            if interaction.user.id in self.roll_history:
                del self.roll_history[interaction.user.id]
            
            # End the session
            session_manager.end_session(interaction.user.id)
            
        except Exception as e:
            logger.error(f"Error in handle_game_over: {e}")
            # Ensure cleanup even on error
            if interaction.user.id in self.active_games:
                del self.active_games[interaction.user.id]
            if interaction.user.id in self.generation_status:
                del self.generation_status[interaction.user.id]
            if interaction.user.id in self.roll_history:
                del self.roll_history[interaction.user.id]
            session_manager.end_session(interaction.user.id)

    async def process_choice(self, interaction: discord.Interaction, choice_text: str, success_rate: int):
        """Process player choice and determine outcome"""
        player = self.active_games[interaction.user.id]
        
        # Roll for success
        if hasattr(self, 'TEST_MODE') and self.TEST_MODE:
            roll = 1  # Always succeeds
            success = True
        else:
            roll = random.randint(1, 100)
            success = roll <= success_rate
        
        # Initialize roll history if needed
        if interaction.user.id not in self.roll_history:
            self.roll_history[interaction.user.id] = []
        
        # Record the roll
        self.roll_history[interaction.user.id].append({
            'scene': player.current_scene_number,
            'roll': roll,
            'required': success_rate,
            'choice': choice_text
        })
        
        # Add to player's choice history for story continuity
        player.choice_history.append({
            'scene': player.current_scene_number,
            'choice': choice_text,
            'outcome': 'success' if success else 'failure',
            'roll': roll
        })
        
        logger.info(f"==== PROCESSING CHOICE ====")
        logger.info(f"Roll: {roll} vs needed {success_rate}")
        
        # Create a much cleaner roll result embed
        roll_embed = discord.Embed(
            title=f"{'✅ Success!' if success else '❌ Failed!'}",
            description=f"**{choice_text}**\n\n"
                       f"Needed: **{success_rate}** or lower | Rolled: **{roll}**\n"
                       f"Lives: {'❤️' * player.lives_remaining}{'🖤' * (player.max_lives - player.lives_remaining)}",
            color=COLORS["SUCCESS"] if success else COLORS["DANGER"]
        )
        
        await interaction.edit_original_response(embed=roll_embed)
        await asyncio.sleep(3.5)  # Give players time to see the result
        
        # Handle failed roll
        if not success:
            player.lives_remaining -= 1
            failure_data = await self.generate_failure_message(player, choice_text, roll, success_rate)
            
            if player.lives_remaining <= 0:
                # Game over - no lives left
                await self.handle_game_over(interaction, player, failure_data["message"])
                return
            
            # Show failure but continue
            life_loss_embed = discord.Embed(
                title="💔 Life Lost",
                description=failure_data["message"],
                color=COLORS["WARNING"]
            )
            life_loss_embed.add_field(
                name="Lives Remaining",
                value=f"{'❤️' * player.lives_remaining}{'🖤' * (player.max_lives - player.lives_remaining)}",
                inline=False
            )
            await interaction.edit_original_response(embed=life_loss_embed, view=None)
            await asyncio.sleep(4)
        
        # Check for victory condition - if this is the final scene and the roll succeeded
        if success and player.current_scene_number == self.MAX_SCENES:
            logger.info("=== VICTORY CONDITION MET ===")
            await self.handle_victory(interaction, player, choice_text)
            return
        
        # Continue to next scene
        logger.debug(f"Full Game State Pre-Choice:")
        logger.debug(f"Player Object: {vars(player)}")
        
        # Generate next scene
        logger.info("=== GENERATING NEXT SCENE ===")
        next_scene = await self.generate_next_scene(
            player,
            choice_text,
            success,
            failure_data["message"] if not success else None
        )
        logger.info(f"Next Scene Generated: {json.dumps(next_scene, indent=2)}")
        
        # Update player's scene
        player.current_scene = next_scene
        player.current_scene_number += 1
        
        # Add victory check after updating scene number - this is the key fix
        if player.current_scene_number > self.MAX_SCENES:
            logger.info("=== VICTORY CONDITION MET ===")
            await self.handle_victory(interaction, player, choice_text)
            return
        
        # Show the new scene
        new_embed = await self.create_game_embed(player)
        new_view = AdventureView(self, player)
        await interaction.edit_original_response(embed=new_embed, view=new_view)
        
        # Log game state after processing
        logger.debug(f"Full Game State Post-Choice:")
        logger.debug(f"Player Object: {vars(player)}")

    async def handle_victory(self, interaction: discord.Interaction, player: Player, final_choice: str):
        """Handle player victory"""
        try:
            logger.info("=== VICTORY CONDITION MET ===")
            
            # Generate victory message
            victory_data = await self.generate_victory_scene(player, final_choice, True)
            
            # Create a more concise victory embed
            embed = discord.Embed(
                title=f"🎉 {victory_data['title']} 🎉",
                description=victory_data["description"],
                color=COLORS["SPECIAL"]  # Special victory color
            )
            
            embed.add_field(
                name="Quest Completed",
                value=f"**{player.quest_name}**\n{victory_data['quest_status']}",
                inline=False
            )
            
            # Keep reward and epilogue but make them more concise
            embed.add_field(
                name="Reward",
                value=f"{victory_data['reward']}\n*{victory_data['epilogue']}*", 
                inline=False
            )
            
            # Simplify journey summary - just show key moments
            journey_summary = "**Key Moments:**\n"
            highlights = []
            
            # Only show first success and last choice
            for choice in player.choice_history:
                if choice["outcome"] == "success" and len(highlights) < 1:
                    highlights.append(f"✅ {choice['choice']}")
            
            # Always show final choice
            if player.choice_history:
                final = player.choice_history[-1]
                result = "✅" if final["outcome"] == "success" else "❌"
                highlights.append(f"{result} {final['choice']}")
                
            journey_summary += "\n".join(highlights)
                
            embed.add_field(
                name="Adventure Summary",
                value=journey_summary,
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

    async def generate_story_structure(self) -> Dict:
        """Generate the initial story structure"""
        try:
            logger.info("Generating story structure...")
            
            structure_prompt = """Create a COMPLETELY UNEXPECTED adventure scenario.

            ABSOLUTELY BANNED TOPICS:
            - NO food, cooking, restaurants, or eating
            - NO service industry or customer service
            - NO generic "save the world" plots
            - NO standard fantasy/sci-fi tropes
            - NO basic AI gone rogue stories
            
            Think WILD situations like:
            - A bureaucratic war between parallel universes over who owns the color blue
            - Debugging a social network where memes have gained sentience and started a cult
            - Fixing a glitch where corporate buzzwords physically manifest as eldritch horrors
            - Managing a crisis where everyone's dreams got converted into cryptocurrency
            - Resolving a dispute between time travelers and their future selves over playlist rights
            - Preventing quantum physics from becoming self-aware and filing for personhood
            - Dealing with a reality where puns have become weapons of mass destruction
            
            CRITICAL RULES:
            1. Must combine UNRELATED concepts in mind-bending ways
            2. Should be both absurd AND logical within its own rules
            3. Must make players think "I can't believe this makes sense"
            4. Dark humor and existential comedy encouraged
            5. Should feel like a Douglas Adams plot on acid
            
            Return ONLY JSON:
            {
                "total_scenes": 5,
                "quest_name": "Title that makes you do a double-take",
                "main_goal": "Objective that sounds insane but follows dream logic",
                "setting": "Location that defies normal space-time",
                "theme_style": "Two conflicting concepts forced together"
            }"""

            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": structure_prompt}],
                response_format={"type": "json_object"},
                temperature=0.8
            )
            
            structure_data = json.loads(response.choices[0].message.content)
            logger.info(f"Generated story structure: {json.dumps(structure_data, indent=2)}")
            return structure_data

        except Exception as e:
            logger.error(f"Error generating story structure: {str(e)}", exc_info=True)
            # Fallback structure if generation fails
            return {
                "total_scenes": 5,
                "quest_name": "Reality.exe Has Stopped Working",
                "main_goal": "Debug the universe before the blue screen of death",
                "setting": "The cosmic command prompt",
                "theme_style": "Tech cosmic horror"
            }

    async def generate_initial_scene(self, structure: Dict) -> Dict:
        """Generate the first scene based on story structure"""
        scene_prompt = f"""Create opening scene for:
        Quest: {structure['quest_name']}
        
        CRITICAL RULES:
        1. Description MUST be ONE SHORT, DRY, WITTY sentence
        2. Think Douglas Adams meets Portal's GLaDOS
        3. NO flowery language or long descriptions
        4. Choices must be under 80 chars and clever
        
        Examples of GOOD descriptions:
        - "The simulation's warranty expired, and reality is showing pop-up ads."
        - "Someone taught AI about existential dread, and now it won't stop posting on Reddit."
        
        Return ONLY JSON:
        {{
            "description": "ONE short, witty sentence",
            "choices": [
                {{"text": "Clever but safe choice", "success_rate": 70}},
                {{"text": "Witty but risky choice", "success_rate": 40}}
            ]
        }}"""

        logger.info(f"=== GENERATING INITIAL SCENE ===")
        logger.info(f"Structure: {json.dumps(structure, indent=2)}")
        logger.info(f"Scene prompt:\n{scene_prompt}")

        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "system", "content": scene_prompt}],
                response_format={"type": "json_object"},
                temperature=0.8
            )
            
            scene_data = json.loads(response.choices[0].message.content)
            logger.info(f"Generated initial scene: {json.dumps(scene_data, indent=2)}")
            
            # Validate choice lengths
            for choice in scene_data["choices"]:
                if len(choice["text"]) > 80:
                    choice["text"] = choice["text"][:77] + "..."
            
            return scene_data

        except Exception as e:
            logger.error(f"Error generating initial scene: {str(e)}", exc_info=True)
            return {
                "description": "Reality glitches around you, presenting two paths forward.",
                "choices": [
                    {"text": "Debug the mainframe", "success_rate": 70},
                    {"text": "Hack the gibson", "success_rate": 40}
                ]
            }

    def get_player(self, user_id: int) -> Optional[Player]:
        """Get a player by their user ID"""
        try:
            if user_id in self.active_games:
                return self.active_games[user_id]
            return None
        except Exception as e:
            logger.error(f"Error in get_player: {str(e)}", exc_info=True)
            return None

# ---- Discord UI and Bot Commands ----

class AdventureView(discord.ui.View):
    """View containing the choice buttons"""
    
    def __init__(self, game: "AdventureGame", player: Player):
        super().__init__(timeout=None)
        self.game = game
        self.player = player
        
        # Add choice buttons with CONSISTENT gray styling
        for i, choice in enumerate(player.current_scene["choices"]):
            # All buttons use the same secondary style (gray)
            button = discord.ui.Button(
                style=discord.ButtonStyle.secondary,  # Gray for all buttons
                label=choice["text"],
                custom_id=f"choice_{i}_{uuid.uuid4()}"
            )
            
            # Add callback for this specific button
            button.callback = self.create_callback(choice["text"], choice["success_rate"])
            self.add_item(button)
    
    def create_callback(self, choice_text, success_rate):
        """Create a callback for the button"""
        async def callback(interaction: discord.Interaction):
            # Disable all buttons and change color of selected button
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    # Disable all buttons
                    item.disabled = True
                    # Highlight the selected button
                    if item.label == choice_text:
                        item.style = discord.ButtonStyle.success
                    else:
                        # Keep other buttons gray but disabled
                        item.style = discord.ButtonStyle.secondary
            
            # Update the message to show disabled buttons with selection highlighted
            await interaction.response.edit_message(view=self)
            
            # Now process the choice
            await self.game.process_choice(interaction, choice_text, success_rate)
            
        return callback

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
        await self.tree.sync()  # Move sync here
        # Start the periodic session cleanup task
        cleanup_sessions.start()

# Initialize instances after all classes are defined
client = MyClient()
game = AdventureGame()
openai_client = OpenAI()

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
    try:
        player = game.get_player(interaction.user.id)
        if not player:
            await interaction.response.send_message("You don't have an active game! Use /start to begin.", ephemeral=True)
            return
            
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
        
    except Exception as e:
        logger.error(f"Error in inventory command: {e}")
        await interaction.response.send_message("An error occurred. Please try again.", ephemeral=True)

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

# Color constants for consistent UI
COLORS = {
    "PRIMARY": 0x3498db,      # Blue - main game color
    "SUCCESS": 0x2ecc71,      # Green - for successes
    "DANGER": 0xe74c3c,       # Red - for failures
    "WARNING": 0xf39c12,      # Orange - for warnings/life loss
    "INFO": 0x95a5a6,         # Gray - for informational messages
    "SPECIAL": 0x9b59b6       # Purple - for special events/victory
}

client.run(os.getenv('DISCORD_TOKEN'))

