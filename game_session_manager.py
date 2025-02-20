from enum import Enum
from typing import Dict, Optional
import discord
from datetime import datetime, timedelta
import logging
import asyncio
import random

logger = logging.getLogger('GameSessionManager')

class SessionState(Enum):
    ACTIVE = "active"
    WARNING = "warning"  # When session is close to timeout
    EXPIRED = "expired"  # When session has timed out
    ENDED = "ended"      # When session is manually ended

class GameSession:
    def __init__(self, user_id: int, channel_id: int):
        self.user_id = user_id
        self.channel_id = channel_id
        self.last_interaction = datetime.now()
        self.message_id: Optional[int] = None
        self.state = SessionState.ACTIVE
        self.warning_sent = False

    def update_interaction(self):
        self.last_interaction = datetime.now()
        self.state = SessionState.ACTIVE
        self.warning_sent = False

    def set_message(self, message_id: int):
        self.message_id = message_id

    def get_state(self, warning_minutes: int, timeout_minutes: int) -> SessionState:
        """
        Determines the current state of the session based on last interaction
        """
        if self.state == SessionState.ENDED:
            return SessionState.ENDED

        time_since_interaction = datetime.now() - self.last_interaction
        
        if time_since_interaction > timedelta(minutes=timeout_minutes):
            self.state = SessionState.EXPIRED
        elif time_since_interaction > timedelta(minutes=warning_minutes):
            self.state = SessionState.WARNING
        
        return self.state

class GameSessionManager:
    def __init__(self, warning_minutes: int = 20, timeout_minutes: int = 30):
        self.sessions: Dict[int, GameSession] = {}
        self.message_to_session: Dict[int, int] = {}
        self.warning_minutes = warning_minutes
        self.timeout_minutes = timeout_minutes
        self.logger = logging.getLogger('GameSessionManager')

    def create_session(self, user_id: int, channel_id: int) -> tuple[bool, str]:
        """
        Attempts to create a new game session for a user.
        Returns (success, message)
        """
        if user_id in self.sessions and self.sessions[user_id].state != SessionState.ENDED:
            return False, "You already have an active game! Use `/end` to end your current game first."
        
        self.sessions[user_id] = GameSession(user_id, channel_id)
        return True, "New game session created successfully!"

    def end_session(self, user_id: int):
        """Ends a user's game session"""
        if user_id in self.sessions:
            session = self.sessions[user_id]
            if session.message_id:
                self.logger.info(f"Cleaning up message {session.message_id} for user {user_id}")
                self.message_to_session.pop(session.message_id, None)
            session.state = SessionState.ENDED
            del self.sessions[user_id]
            self.logger.info(f"Session ended for user {user_id}")

    def get_session(self, user_id: int) -> Optional[GameSession]:
        """Gets an active session for a user"""
        if user_id in self.sessions and self.sessions[user_id].state != SessionState.ENDED:
            session = self.sessions[user_id]
            session.update_interaction()
            return session
        return None

    def register_message(self, user_id: int, message_id: int):
        """Associates a message with a user's session"""
        if user_id in self.sessions:
            self.logger.info(f"Registering message {message_id} for user {user_id}")
            self.sessions[user_id].set_message(message_id)
            self.message_to_session[message_id] = user_id
        else:
            self.logger.warning(f"Attempted to register message for non-existent session: user={user_id}, message={message_id}")

    async def check_sessions(self, client: discord.Client) -> list[int]:
        """
        Checks all sessions and sends warnings or expires them as needed.
        Returns list of expired session user IDs.
        """
        expired_sessions = []
        
        for user_id, session in self.sessions.items():
            state = session.get_state(self.warning_minutes, self.timeout_minutes)
            
            if state == SessionState.WARNING and not session.warning_sent:
                try:
                    channel = client.get_channel(session.channel_id)
                    if channel:
                        await channel.send(
                            f"<@{user_id}> Your game session will expire in "
                            f"{self.timeout_minutes - self.warning_minutes} minutes due to inactivity. "
                            "Make a move to keep playing!",
                            delete_after=300  # Delete after 5 minutes
                        )
                        session.warning_sent = True
                except Exception as e:
                    logger.error(f"Failed to send warning message: {e}")

            elif state == SessionState.EXPIRED:
                expired_sessions.append(user_id)
                try:
                    channel = client.get_channel(session.channel_id)
                    if channel:
                        await channel.send(
                            f"<@{user_id}> Your game session has expired due to inactivity. "
                            "Use `/start` to begin a new game!",
                            delete_after=300  # Delete after 5 minutes
                        )
                except Exception as e:
                    logger.error(f"Failed to send expiration message: {e}")

        # Clean up expired sessions
        for user_id in expired_sessions:
            self.end_session(user_id)

        return expired_sessions

    async def handle_interaction(self, interaction: discord.Interaction) -> tuple[bool, str]:
        """
        Handles button interactions, checking if they're valid.
        Returns (should_process, message)
        """
        message_id = interaction.message.id
        self.logger.debug(f"Handling interaction for message {message_id} from user {interaction.user.id}")
        self.logger.debug(f"Current message mappings: {self.message_to_session}")
        
        if message_id not in self.message_to_session:
            self.logger.warning(f"Message {message_id} not found in message_to_session mapping")
            return False, "This game session is no longer active."
            
        session_user_id = self.message_to_session[message_id]
        
        if interaction.user.id != session_user_id:
            self.logger.warning(f"User {interaction.user.id} attempted to interact with session owned by {session_user_id}")
            return False, "This is not your game session!"
            
        session = self.get_session(session_user_id)
        if not session:
            self.logger.warning(f"Session not found for user {session_user_id}")
            return False, "This game session has expired. Please start a new game."

        state = session.get_state(self.warning_minutes, self.timeout_minutes)
        if state == SessionState.EXPIRED:
            self.logger.info(f"Session expired for user {session_user_id}")
            self.end_session(session_user_id)
            return False, "This game session has expired due to inactivity. Please start a new game."
            
        session.update_interaction()
        return True, ""

class ChoiceButton(discord.ui.Button):
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

            if not success:
                view.player.lives_remaining -= 1
                failure_data = await view.game.generate_failure_message(
                    view.player,
                    self.label,
                    roll,
                    self.success_rate
                )
                
                if view.player.lives_remaining <= 0:
                    # Game Over - No lives left
                    await view.game.handle_game_over(interaction, view.player, failure_data["message"])
                    return

                # Show failure but continue
                failure_embed = discord.Embed(
                    title="💔 Life Lost!",
                    description=failure_data["message"],
                    color=0xff7700
                )
                failure_embed.add_field(
                    name="Consequence",
                    value=f"Lives Remaining: {'❤️' * view.player.lives_remaining}\nDealing with the aftermath...",
                    inline=False
                )
                await interaction.edit_original_response(embed=failure_embed, view=None)
                await asyncio.sleep(3)

            # Generate next scene
            next_scene = await view.game.generate_next_scene(
                view.player,
                self.label,
                success,
                failure_data["message"] if not success else None
            )
            
            # Increment scene number
            view.player.current_scene_number += 1
            
            # Check for victory condition
            if view.player.current_scene_number > view.game.MAX_SCENES:
                victory_scene = await view.game.generate_victory_scene(
                    view.player,
                    self.label
                )
                view.player.current_scene = victory_scene
                
                victory_embed = await view.game.create_game_embed(view.player)
                victory_embed.add_field(
                    name="🎉 Mission Complete!",
                    value=victory_scene["description"],
                    inline=False
                )
                await interaction.edit_original_response(embed=victory_embed, view=None)
                
                # Clean up completed game
                if interaction.user.id in view.game.active_games:
                    del view.game.active_games[interaction.user.id]
                return
            
            # Continue to next scene
            view.player.current_scene = next_scene
            new_embed = await view.game.create_game_embed(view.player)
            await interaction.edit_original_response(
                embed=new_embed,
                view=AdventureView(view.game, view.player)
            )

        except Exception as e:
            logger.error(f"Error in button callback: {str(e)}")
            try:
                await interaction.followup.send("An error occurred.", ephemeral=True)
            except:
                logger.error("Failed to send error message") 