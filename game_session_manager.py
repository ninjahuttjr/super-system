from enum import Enum
from typing import Dict, Optional
import discord
from datetime import datetime, timedelta
import logging

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