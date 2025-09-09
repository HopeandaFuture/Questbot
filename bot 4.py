import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import asyncio
from typing import Optional, Dict, List
import json
import requests
import os
import webserver

# Bot configuration
TOKEN = None  # Set this through environment variables
PREFIX = '-'

# XP Level thresholds
LEVEL_THRESHOLDS = {
    1: 0,
    2: 100,
    3: 500,
    4: 1200,
    5: 2200,
    6: 3500,
    7: 5100,
    8: 7000,
    9: 9200,
    10: 11700
}

# Bot setup - With message content intent for full functionality
# NOTE: Requires "Message Content Intent" enabled in Discord Developer Portal
intents = discord.Intents.none()
intents.guilds = True
intents.guild_messages = True
intents.guild_reactions = True
intents.message_content = True  # Privileged intent - enable in Discord Developer Portal

bot = commands.Bot(command_prefix=PREFIX, intents=intents)

class QuestBot:
    def __init__(self):
        self.db_connection = None
        self.quest_ping_role_id = None
        self.quest_channel_id = None
        self.role_xp_assignments = {}
        self.init_database()
    
    def init_database(self):
        """Initialize SQLite database for storing user XP and quest data"""
        self.db_connection = sqlite3.connect('quest_bot.db')
        cursor = self.db_connection.cursor()
        
        # Create users table for XP tracking
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                UNIQUE(user_id, guild_id)
            )
        ''')
        
        # Create quests table for active quests
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS quests (
                message_id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                channel_id INTEGER,
                title TEXT,
                content TEXT,
                completed_users TEXT DEFAULT '[]'
            )
        ''')
        
        # Create settings table for bot configuration
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                guild_id INTEGER PRIMARY KEY,
                quest_ping_role_id INTEGER,
                quest_channel_id INTEGER,
                role_xp_assignments TEXT DEFAULT '{}'
            )
        ''')
        
        self.db_connection.commit()
    
    def get_user_data(self, user_id: int, guild_id: int):
        """Get user XP and level data"""
        if not self.db_connection:
            return {'xp': 0, 'level': 1}
        cursor = self.db_connection.cursor()
        cursor.execute('SELECT xp, level FROM users WHERE user_id = ? AND guild_id = ?', (user_id, guild_id))
        result = cursor.fetchone()
        if result:
            return {'xp': result[0], 'level': result[1]}
        else:
            # Create new user entry
            cursor.execute('INSERT INTO users (user_id, guild_id, xp, level) VALUES (?, ?, 0, 1)', (user_id, guild_id))
            self.db_connection.commit()
            return {'xp': 0, 'level': 1}
    
    def update_user_xp(self, user_id: int, guild_id: int, xp_change: int):
        """Update user XP and recalculate level"""
        if not self.db_connection:
            return 0, 1
        cursor = self.db_connection.cursor()
        current_data = self.get_user_data(user_id, guild_id)
        old_level = current_data['level']
        new_xp = max(0, current_data['xp'] + xp_change)
        new_level = self.calculate_level(new_xp)
        
        cursor.execute('UPDATE users SET xp = ?, level = ? WHERE user_id = ? AND guild_id = ?', 
                      (new_xp, new_level, user_id, guild_id))
        self.db_connection.commit()
        
        # Handle level role assignment if level changed
        if old_level != new_level:
            asyncio.create_task(self.update_user_level_role(user_id, guild_id, old_level, new_level))
        
        return new_xp, new_level
    
    async def create_level_roles(self, guild):
        """Create level roles if they don't exist"""
        try:
            for level in range(1, 11):
                role_name = f"Level {level}"
                # Check if role already exists
                existing_role = discord.utils.get(guild.roles, name=role_name)
                if not existing_role:
                    # Create role with a color gradient from blue to gold
                    color_value = int(0x0099ff + (0xffd700 - 0x0099ff) * (level - 1) / 9)
                    await guild.create_role(
                        name=role_name,
                        color=discord.Color(color_value),
                        reason=f"Auto-created level role for Level {level}"
                    )
                    print(f"Created role: {role_name}")
        except discord.Forbidden:
            print("Bot lacks permission to create roles")
        except Exception as e:
            print(f"Error creating level roles: {e}")
    
    async def update_user_level_role(self, user_id: int, guild_id: int, old_level: int, new_level: int):
        """Update user's level role when they level up/down"""
        try:
            guild = bot.get_guild(guild_id)
            if not guild:
                return
            
            member = guild.get_member(user_id)
            if not member:
                return
            
            # Remove old level role if it exists
            old_role_name = f"Level {old_level}"
            old_role = discord.utils.get(guild.roles, name=old_role_name)
            if old_role and old_role in member.roles:
                await member.remove_roles(old_role, reason="Level changed")
            
            # Add new level role
            new_role_name = f"Level {new_level}"
            new_role = discord.utils.get(guild.roles, name=new_role_name)
            if new_role:
                await member.add_roles(new_role, reason=f"Reached {new_role_name}")
            else:
                # Create the role if it doesn't exist
                await self.create_level_roles(guild)
                new_role = discord.utils.get(guild.roles, name=new_role_name)
                if new_role:
                    await member.add_roles(new_role, reason=f"Reached {new_role_name}")
            
            print(f"Updated {member.display_name}: {old_role_name} -> {new_role_name}")
        except discord.Forbidden:
            print("Bot lacks permission to manage roles")
        except Exception as e:
            print(f"Error updating user level role: {e}")
    
    def calculate_level(self, xp: int) -> int:
        """Calculate level based on XP"""
        for level in range(10, 0, -1):
            if xp >= LEVEL_THRESHOLDS[level]:
                return level
        return 1
    
    def calculate_total_user_xp(self, user_id: int, guild_id: int) -> int:
        """Calculate total XP including quest XP + role-based XP"""
        try:
            guild = bot.get_guild(guild_id)
            if not guild:
                # Fall back to database XP if guild not found
                user_data = self.get_user_data(user_id, guild_id)
                return user_data.get('xp', 0)
            
            member = guild.get_member(user_id)
            if not member:
                # Fall back to database XP if member not found
                user_data = self.get_user_data(user_id, guild_id)
                return user_data.get('xp', 0)
            
            total_xp = 0
            
            # Get base XP from database (quest completions and manual additions)
            user_data = self.get_user_data(user_id, guild_id)
            base_xp = user_data.get('xp', 0)
            
            # Add XP from level roles (if user has a level role, add its minimum XP)
            level_role_xp = 0
            for role in member.roles:
                if role.name.startswith("Level "):
                    try:
                        level_num = int(role.name.split("Level ")[1])
                        if 1 <= level_num <= 10:
                            level_role_xp = max(level_role_xp, LEVEL_THRESHOLDS.get(level_num, 0))
                    except:
                        continue
            
            # Add XP from custom assigned roles
            custom_role_xp = 0
            if guild_id in self.role_xp_assignments:
                role_assignments = self.role_xp_assignments[guild_id]
                for role in member.roles:
                    if str(role.id) in role_assignments:
                        custom_role_xp += role_assignments[str(role.id)]
            
            # Add XP from automatically detected badge and streak roles
            auto_role_xp = 0
            for role in member.roles:
                role_name_lower = role.name.lower()
                # Badge roles give 5 XP each
                if "badge" in role_name_lower:
                    auto_role_xp += 5
                # Streak roles give 5 XP each  
                elif "streak" in role_name_lower:
                    auto_role_xp += 5
            
            # Total XP is the maximum of: base XP OR (level role XP + custom role XP + auto role XP)
            # This ensures users get credit for their roles even if manually assigned
            total_xp = max(base_xp, level_role_xp + custom_role_xp + auto_role_xp)
            
            print(f"XP calculation for user {user_id}: base={base_xp}, level_role={level_role_xp}, custom_role={custom_role_xp}, auto_role={auto_role_xp}, total={total_xp}")
            
            return total_xp
            
        except Exception as e:
            print(f"Error calculating total XP for user {user_id}: {e}")
            # Fall back to database XP
            user_data = self.get_user_data(user_id, guild_id)
            return user_data.get('xp', 0)
    
    def get_leaderboard(self, guild_id: int, limit: int = 10):
        """Get top users for leaderboard"""
        if not self.db_connection:
            return []
        cursor = self.db_connection.cursor()
        cursor.execute('SELECT user_id, xp, level FROM users WHERE guild_id = ? ORDER BY xp DESC LIMIT ?', 
                      (guild_id, limit))
        return cursor.fetchall()
    
    def save_settings(self, guild_id: int):
        """Save bot settings to database"""
        if not self.db_connection:
            return
        cursor = self.db_connection.cursor()
        role_xp_json = json.dumps(self.role_xp_assignments.get(guild_id, {}))
        cursor.execute('''
            INSERT OR REPLACE INTO settings 
            (guild_id, quest_ping_role_id, quest_channel_id, role_xp_assignments) 
            VALUES (?, ?, ?, ?)
        ''', (guild_id, self.quest_ping_role_id, self.quest_channel_id, role_xp_json))
        self.db_connection.commit()
    
    def load_settings(self, guild_id: int):
        """Load bot settings from database"""
        if not self.db_connection:
            return
        cursor = self.db_connection.cursor()
        cursor.execute('SELECT quest_ping_role_id, quest_channel_id, role_xp_assignments FROM settings WHERE guild_id = ?', (guild_id,))
        result = cursor.fetchone()
        if result:
            self.quest_ping_role_id = result[0]
            self.quest_channel_id = result[1]
            self.role_xp_assignments[guild_id] = json.loads(result[2])

quest_bot = QuestBot()

@bot.event
async def on_ready():
    print(f'{bot.user} has logged in to Discord!')
    for guild in bot.guilds:
        quest_bot.load_settings(guild.id)
        # Create level roles on startup
        await quest_bot.create_level_roles(guild)
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")

@bot.event
async def on_reaction_add(reaction, user):
    """Handle quest completion reactions"""
    if user.bot:
        return
    
    # Check if it's a quest completion (✅ emoji)
    if str(reaction.emoji) == '✅':
        if not quest_bot.db_connection:
            return
        cursor = quest_bot.db_connection.cursor()
        cursor.execute('SELECT title, completed_users FROM quests WHERE message_id = ?', (reaction.message.id,))
        quest_data = cursor.fetchone()
        
        if quest_data:
            title, completed_users_json = quest_data
            completed_users = json.loads(completed_users_json)
            
            if user.id not in completed_users:
                # Award 50 XP for quest completion
                new_xp, new_level = quest_bot.update_user_xp(user.id, reaction.message.guild.id, 50)
                completed_users.append(user.id)
                
                # Update quest completion list
                cursor.execute('UPDATE quests SET completed_users = ? WHERE message_id = ?', 
                              (json.dumps(completed_users), reaction.message.id))
                if quest_bot.db_connection:
                    quest_bot.db_connection.commit()
                
                # Send confirmation message
                embed = discord.Embed(
                    title="Quest Completed!",
                    description=f"{user.mention} completed: **{title}**\n+50 XP (Total: {new_xp} XP, Level {new_level})",
                    color=0x00ff00
                )
                await reaction.message.channel.send(embed=embed, delete_after=10)

@bot.event
async def on_member_update(before, after):
    """Handle role changes for automatic XP assignment"""
    # Check for badge roles (5 XP each)
    # Check for streak roles (5 XP each for new roles)
    guild_id = after.guild.id
    
    if guild_id in quest_bot.role_xp_assignments:
        role_assignments = quest_bot.role_xp_assignments[guild_id]
        
        # Check for new roles added
        new_roles = set(after.roles) - set(before.roles)
        for role in new_roles:
            if str(role.id) in role_assignments:
                xp_reward = role_assignments[str(role.id)]
                new_xp, new_level = quest_bot.update_user_xp(after.id, guild_id, xp_reward)
                
                # Send notification
                embed = discord.Embed(
                    title="Role XP Awarded!",
                    description=f"{after.mention} gained **{role.name}** role!\n+{xp_reward} XP (Total: {new_xp} XP, Level {new_level})",
                    color=0x0099ff
                )
                # Try to send to general channel or first available channel
                for channel in after.guild.text_channels:
                    if hasattr(channel, 'send') and channel.permissions_for(after.guild.me).send_messages:
                        await channel.send(embed=embed, delete_after=15)
                        break

@bot.command(name='addquest')
@commands.has_permissions(manage_messages=True)
async def add_quest(ctx, title: str, *, content: str):
    """Add a new quest embed"""
    embed = discord.Embed(
        title=f"🎯 Quest: {title}",
        description=content,
        color=0xff9900
    )
    embed.add_field(name="Reward", value="50 XP", inline=True)
    embed.add_field(name="Complete", value="React with ✅", inline=True)
    embed.set_footer(text="React with ✅ to mark this quest as complete!")
    
    # Send to quest channel if set, otherwise current channel
    channel_id = quest_bot.quest_channel_id
    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel:
            quest_message = await channel.send(embed=embed)
        else:
            quest_message = await ctx.send(embed=embed)
    else:
        quest_message = await ctx.send(embed=embed)
    
    # Add checkmark reaction
    await quest_message.add_reaction('✅')
    
    # Ping quest role - first check manual setting, then auto-find @Quests role
    quest_role = None
    if quest_bot.quest_ping_role_id:
        quest_role = ctx.guild.get_role(quest_bot.quest_ping_role_id)
    
    if not quest_role:
        # Auto-find @Quests role
        quest_role = discord.utils.get(ctx.guild.roles, name="Quests")
    
    if quest_role:
        ping_msg = await quest_message.channel.send(f"{quest_role.mention} New quest available!")
        await asyncio.sleep(2)
        await ping_msg.delete()
    
    # Save quest to database
    if quest_bot.db_connection:
        cursor = quest_bot.db_connection.cursor()
        cursor.execute('INSERT INTO quests (message_id, guild_id, channel_id, title, content) VALUES (?, ?, ?, ?, ?)',
                      (quest_message.id, ctx.guild.id, quest_message.channel.id, title, content))
        quest_bot.db_connection.commit()
    
    await ctx.message.delete()

@bot.command(name='removequest')
@commands.has_permissions(manage_messages=True)
async def remove_quest(ctx, message_id: int):
    """Remove a quest by message ID"""
    try:
        # Remove from database
        if quest_bot.db_connection:
            cursor = quest_bot.db_connection.cursor()
            cursor.execute('DELETE FROM quests WHERE message_id = ?', (message_id,))
            quest_bot.db_connection.commit()
        
        # Try to delete the message
        try:
            message = await ctx.fetch_message(message_id)
            await message.delete()
        except:
            pass
        
        await ctx.send("✅ Quest removed successfully!", delete_after=5)
    except Exception as e:
        await ctx.send("❌ Failed to remove quest. Make sure the message ID is correct.", delete_after=5)

@bot.command(name='questping')
@commands.has_permissions(manage_roles=True)
async def set_quest_ping(ctx, role_id: int):
    """Set the role to ping for new quests"""
    role = ctx.guild.get_role(role_id)
    if role:
        quest_bot.quest_ping_role_id = role_id
        quest_bot.save_settings(ctx.guild.id)
        await ctx.send(f"✅ Quest ping role set to: {role.mention}", delete_after=5)
    else:
        await ctx.send("❌ Role not found!", delete_after=5)

@bot.command(name='questchannel')
@commands.has_permissions(manage_channels=True)
async def set_quest_channel(ctx, channel_id: int):
    """Set the channel for quest embeds"""
    channel = bot.get_channel(channel_id)
    if channel:
        quest_bot.quest_channel_id = channel_id
        quest_bot.save_settings(ctx.guild.id)
        await ctx.send(f"✅ Quest channel set to: {channel.mention}", delete_after=5)
    else:
        await ctx.send("❌ Channel not found!", delete_after=5)

@bot.command(name='addXP')
@commands.has_any_role('staff', 'Staff', 'STAFF', 'admin', 'Admin', 'ADMIN')
async def add_xp(ctx, member: discord.Member, amount: int):
    """Add XP to a member"""
    new_xp, new_level = quest_bot.update_user_xp(member.id, ctx.guild.id, amount)
    
    embed = discord.Embed(
        title="XP Added",
        description=f"Added {amount} XP to {member.mention}\nNew Total: {new_xp} XP (Level {new_level})",
        color=0x00ff00
    )
    await ctx.send(embed=embed)

@bot.command(name='removeXP')
@commands.has_any_role('staff', 'Staff', 'STAFF', 'admin', 'Admin', 'ADMIN')
async def remove_xp(ctx, member: discord.Member, amount: int):
    """Remove XP from a member"""
    new_xp, new_level = quest_bot.update_user_xp(member.id, ctx.guild.id, -amount)
    
    embed = discord.Embed(
        title="XP Removed",
        description=f"Removed {amount} XP from {member.mention}\nNew Total: {new_xp} XP (Level {new_level})",
        color=0xff0000
    )
    await ctx.send(embed=embed)

@bot.command(name='assignroleXP')
@commands.has_permissions(manage_roles=True)
async def assign_role_xp(ctx, role: discord.Role, xp_amount: int):
    """Assign XP value to a role"""
    guild_id = ctx.guild.id
    if guild_id not in quest_bot.role_xp_assignments:
        quest_bot.role_xp_assignments[guild_id] = {}
    
    quest_bot.role_xp_assignments[guild_id][str(role.id)] = xp_amount
    quest_bot.save_settings(guild_id)
    
    embed = discord.Embed(
        title="Role XP Assignment",
        description=f"Role **{role.name}** now awards {xp_amount} XP when obtained",
        color=0x0099ff
    )
    await ctx.send(embed=embed)

@bot.command(name='leaderboard')
async def leaderboard(ctx):
    """Display the XP leaderboard"""
    try:
        leaderboard_data = quest_bot.get_leaderboard(ctx.guild.id, 10)
        print(f"Leaderboard data retrieved: {leaderboard_data}")  # Debug print
        
        if not leaderboard_data:
            embed = discord.Embed(
                title="🏆 XP Leaderboard",
                description="No users with XP found yet!\nComplete some quests to get on the leaderboard!",
                color=0xffd700
            )
            # Still show level requirements
            level_info = "**Level Requirements:**\n"
            for level, xp in LEVEL_THRESHOLDS.items():
                level_info += f"Level {level}: {xp:,} XP\n"
            embed.add_field(name="Level System", value=level_info, inline=False)
            await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="🏆 XP Leaderboard",
            description="Top 10 Quest Completers",
            color=0xffd700
        )
        
        medals = ["🥇", "🥈", "🥉"]
        users_added = 0
        
        for i, (user_id, xp, level) in enumerate(leaderboard_data):
            medal = medals[i] if i < 3 else f"#{i+1}"
            
            # Try multiple methods to get user info
            user = ctx.guild.get_member(user_id)
            if not user:
                user = bot.get_user(user_id)
            
            # Calculate total XP including role-based XP
            total_xp = quest_bot.calculate_total_user_xp(user_id, ctx.guild.id)
            
            if user:
                # Format username without pinging - use @ but escape it
                username = f"@{user.name}"
                display_name = getattr(user, 'display_name', user.name)
                if display_name != user.name:
                    username = f"@{user.name} ({display_name})"
                
                embed.add_field(
                    name=f"{medal} Level {level}",
                    value=f"{username}\n{total_xp:,} XP",
                    inline=True
                )
                users_added += 1
            else:
                # Try to fetch user info from Discord API
                try:
                    user = await bot.fetch_user(user_id)
                    username = f"@{user.name}"
                    embed.add_field(
                        name=f"{medal} Level {level}",
                        value=f"{username}\n{total_xp:,} XP",
                        inline=True
                    )
                    users_added += 1
                except:
                    # Last resort - show user ID
                    embed.add_field(
                        name=f"{medal} Level {level}",
                        value=f"@User{str(user_id)[-4:]}\n{total_xp:,} XP",
                        inline=True
                    )
                    users_added += 1
        
        if users_added == 0:
            embed.add_field(
                name="No Active Users", 
                value="Users with XP may have left the server", 
                inline=False
            )
        
        # Add level requirements info
        level_info = "**Level Requirements:**\n"
        for level, xp_req in LEVEL_THRESHOLDS.items():
            level_info += f"Level {level}: {xp_req:,} XP\n"
        
        embed.add_field(name="Level System", value=level_info, inline=False)
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"Error in leaderboard command: {e}")
        await ctx.send("❌ Could not retrieve leaderboard data. Please try again later.", delete_after=5)

@bot.command(name='questbot')
async def questbot_ping(ctx):
    """Ping the bot to check if it's online"""
    await ctx.send("online")

@bot.command(name='checkXP')
async def check_xp(ctx, member: discord.Member = None):
    """Check your current XP and level progress"""
    try:
        # If no member specified, check the command user's XP
        target_member = member or ctx.author
        
        # Get total XP including role-based XP
        current_xp = quest_bot.calculate_total_user_xp(target_member.id, ctx.guild.id)
        current_level = quest_bot.calculate_level(current_xp)
        
        # Calculate XP needed for next level
        next_level = min(current_level + 1, 10)  # Cap at level 10
        next_level_xp = LEVEL_THRESHOLDS.get(next_level, LEVEL_THRESHOLDS[10])
        xp_needed = max(0, next_level_xp - current_xp)
        
        # Calculate progress percentage safely
        if current_level < 10:
            current_level_xp = LEVEL_THRESHOLDS.get(current_level, 0)
            xp_range = next_level_xp - current_level_xp
            if xp_range > 0:
                progress_percentage = min(100, max(0, ((current_xp - current_level_xp) / xp_range) * 100))
            else:
                progress_percentage = 100
        else:
            progress_percentage = 100
        
        embed = discord.Embed(
            title=f"📊 {target_member.display_name}'s XP Stats",
            color=0x00ff00
        )
        
        embed.add_field(name="💰 Current XP", value=f"{current_xp:,} XP", inline=True)
        embed.add_field(name="⭐ Current Level", value=f"Level {current_level}", inline=True)
        
        if current_level < 10:
            embed.add_field(name="🎯 XP to Next Level", value=f"{xp_needed:,} XP needed", inline=True)
            
            # Progress bar with safe calculation
            progress_bar_length = 20
            filled_length = int(progress_bar_length * progress_percentage / 100)
            filled_length = max(0, min(filled_length, progress_bar_length))  # Clamp values
            bar = "█" * filled_length + "░" * (progress_bar_length - filled_length)
            embed.add_field(
                name="📈 Progress to Next Level", 
                value=f"`{bar}` {progress_percentage:.1f}%", 
                inline=False
            )
        else:
            embed.add_field(name="🏆 Status", value="**MAX LEVEL REACHED!**", inline=True)
        
        # Safe avatar handling
        try:
            if target_member.avatar:
                embed.set_thumbnail(url=target_member.avatar.url)
            else:
                embed.set_thumbnail(url=target_member.default_avatar.url)
        except:
            pass  # Skip thumbnail if there are issues
        
        embed.set_footer(text="Complete quests and gain roles to earn XP!")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        print(f"Error in checkXP command: {e}")
        await ctx.send("❌ Could not retrieve XP data. Please try again later.", delete_after=5)

@bot.command(name='commands')
async def show_commands(ctx):
    """Display all available bot commands"""
    embed = discord.Embed(
        title="🤖 QuestBot Commands",
        description="All available commands for the Discord Quest Bot",
        color=0x0099ff
    )
    
    # Prefix Commands
    prefix_commands = """
    **Quest Management:**
    `-addquest <title> <content>` - Create new quest embed
    `-removequest <message_id>` - Delete quest by message ID
    `-questping <role_id>` - Set quest ping role
    `-questchannel <channel_id>` - Set quest channel
    
    **XP Management (Staff Only):**
    `-addXP <member> <amount>` - Add XP to user
    `-removeXP <member> <amount>` - Remove XP from user
    `-assignroleXP <role> <amount>` - Assign XP value to role
    
    **General:**
    `-leaderboard` - Display XP rankings
    `-checkXP [@member]` - Check your or someone's XP
    `-questbot` - Ping bot to check if online
    `-commands` - Show this command list
    """
    
    slash_commands = """
    **Quest Management:**
    `/addquest` - Create new quest embed
    `/removequest` - Delete quest by message ID
    `/questping` - Set quest ping role
    `/questchannel` - Set quest channel
    
    **XP Management (Staff Only):**
    `/addxp` - Add XP to user
    `/removexp` - Remove XP from user
    `/assignrolexp` - Assign XP value to role
    
    **Level Roles (Admin Only):**
    `/createlevelroles` - Create all Level 1-10 roles
    `/assignlevelroles` - Assign level roles to all users
    
    **General:**
    `/leaderboard` - Display XP rankings
    `/questbot` - Ping bot to check if online
    """
    
    embed.add_field(name="📝 Prefix Commands (using -)", value=prefix_commands, inline=False)
    embed.add_field(name="⚡ Slash Commands (using /)", value=slash_commands, inline=False)
    
    embed.add_field(
        name="🏆 Level System", 
        value="Earn XP by completing quests (50 XP each) and gaining roles!\nLevel roles are automatically assigned based on your XP.",
        inline=False
    )
    
    embed.set_footer(text="Use either - or / commands • Both work the same way!")
    
    await ctx.send(embed=embed)

# Slash Commands
@bot.tree.command(name="questbot", description="Ping the bot to check if it's online")
async def slash_questbot_ping(interaction: discord.Interaction):
    await interaction.response.send_message("online")

@bot.tree.command(name="addquest", description="Create a new quest embed")
@app_commands.describe(title="Quest title", content="Quest description")
async def slash_add_quest(interaction: discord.Interaction, title: str, content: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You need Manage Messages permission to use this command!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"🎯 Quest: {title}",
        description=content,
        color=0xff9900
    )
    embed.add_field(name="Reward", value="50 XP", inline=True)
    embed.add_field(name="Complete", value="React with ✅", inline=True)
    embed.set_footer(text="React with ✅ to mark this quest as complete!")
    
    # Send to quest channel if set, otherwise current channel
    channel_id = quest_bot.quest_channel_id
    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel and hasattr(channel, 'send'):
            quest_message = await channel.send(embed=embed)
        else:
            quest_message = await interaction.followup.send(embed=embed)
    else:
        await interaction.response.send_message(embed=embed)
        quest_message = await interaction.original_response()
    
    # Add checkmark reaction
    await quest_message.add_reaction('✅')
    
    # Ping quest role - first check manual setting, then auto-find @Quests role
    quest_role = None
    if quest_bot.quest_ping_role_id:
        quest_role = interaction.guild.get_role(quest_bot.quest_ping_role_id)
    
    if not quest_role:
        # Auto-find @Quests role
        quest_role = discord.utils.get(interaction.guild.roles, name="Quests")
    
    if quest_role:
        ping_msg = await quest_message.channel.send(f"{quest_role.mention} New quest available!")
        await asyncio.sleep(2)
        await ping_msg.delete()
    
    # Save quest to database
    if quest_bot.db_connection:
        cursor = quest_bot.db_connection.cursor()
        cursor.execute('INSERT INTO quests (message_id, guild_id, channel_id, title, content) VALUES (?, ?, ?, ?, ?)',
                      (quest_message.id, interaction.guild.id, quest_message.channel.id, title, content))
        quest_bot.db_connection.commit()
    
    if not channel_id or not channel or not hasattr(channel, 'send'):
        await interaction.response.send_message("✅ Quest created!", ephemeral=True)

@bot.tree.command(name="removequest", description="Remove a quest by message ID")
@app_commands.describe(message_id="ID of the quest message to remove")
async def slash_remove_quest(interaction: discord.Interaction, message_id: str):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ You need Manage Messages permission to use this command!", ephemeral=True)
        return
    
    try:
        msg_id = int(message_id)
        # Remove from database
        if quest_bot.db_connection:
            cursor = quest_bot.db_connection.cursor()
            cursor.execute('DELETE FROM quests WHERE message_id = ?', (msg_id,))
            quest_bot.db_connection.commit()
        
        # Try to delete the message
        try:
            message = await interaction.channel.fetch_message(msg_id)
            await message.delete()
        except:
            pass
        
        await interaction.response.send_message("✅ Quest removed successfully!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message("❌ Failed to remove quest. Make sure the message ID is correct.", ephemeral=True)

@bot.tree.command(name="questping", description="Set the role to ping for new quests")
@app_commands.describe(role="Role to ping for quests")
async def slash_set_quest_ping(interaction: discord.Interaction, role: discord.Role):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission to use this command!", ephemeral=True)
        return
    
    quest_bot.quest_ping_role_id = role.id
    quest_bot.save_settings(interaction.guild.id)
    await interaction.response.send_message(f"✅ Quest ping role set to: {role.mention}", ephemeral=True)

@bot.tree.command(name="questchannel", description="Set the channel for quest embeds")
@app_commands.describe(channel="Channel for quest embeds")
async def slash_set_quest_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ You need Manage Channels permission to use this command!", ephemeral=True)
        return
    
    quest_bot.quest_channel_id = channel.id
    quest_bot.save_settings(interaction.guild.id)
    await interaction.response.send_message(f"✅ Quest channel set to: {channel.mention}", ephemeral=True)

@bot.tree.command(name="addxp", description="Add XP to a member")
@app_commands.describe(member="Member to add XP to", amount="Amount of XP to add")
async def slash_add_xp(interaction: discord.Interaction, member: discord.Member, amount: int):
    # Check if user has staff role
    staff_roles = ['staff', 'Staff', 'STAFF', 'admin', 'Admin', 'ADMIN']
    if not any(role.name in staff_roles for role in interaction.user.roles):
        await interaction.response.send_message("❌ You need the @staff role to use this command!", ephemeral=True)
        return
    
    new_xp, new_level = quest_bot.update_user_xp(member.id, interaction.guild.id, amount)
    
    embed = discord.Embed(
        title="XP Added",
        description=f"Added {amount} XP to {member.mention}\nNew Total: {new_xp} XP (Level {new_level})",
        color=0x00ff00
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removexp", description="Remove XP from a member")
@app_commands.describe(member="Member to remove XP from", amount="Amount of XP to remove")
async def slash_remove_xp(interaction: discord.Interaction, member: discord.Member, amount: int):
    # Check if user has staff role
    staff_roles = ['staff', 'Staff', 'STAFF', 'admin', 'Admin', 'ADMIN']
    if not any(role.name in staff_roles for role in interaction.user.roles):
        await interaction.response.send_message("❌ You need the @staff role to use this command!", ephemeral=True)
        return
    
    new_xp, new_level = quest_bot.update_user_xp(member.id, interaction.guild.id, -amount)
    
    embed = discord.Embed(
        title="XP Removed",
        description=f"Removed {amount} XP from {member.mention}\nNew Total: {new_xp} XP (Level {new_level})",
        color=0xff0000
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="assignrolexp", description="Assign XP value to a role")
@app_commands.describe(role="Role to assign XP to", xp_amount="XP amount for this role")
async def slash_assign_role_xp(interaction: discord.Interaction, role: discord.Role, xp_amount: int):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission to use this command!", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    if guild_id not in quest_bot.role_xp_assignments:
        quest_bot.role_xp_assignments[guild_id] = {}
    
    quest_bot.role_xp_assignments[guild_id][str(role.id)] = xp_amount
    quest_bot.save_settings(guild_id)
    
    embed = discord.Embed(
        title="Role XP Assignment",
        description=f"Role **{role.name}** now awards {xp_amount} XP when obtained",
        color=0x0099ff
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Display the XP leaderboard")
async def slash_leaderboard(interaction: discord.Interaction):
    try:
        leaderboard_data = quest_bot.get_leaderboard(interaction.guild.id, 10)
        print(f"Slash leaderboard data retrieved: {leaderboard_data}")  # Debug print
        
        if not leaderboard_data:
            embed = discord.Embed(
                title="🏆 XP Leaderboard",
                description="No users with XP found yet!\nComplete some quests to get on the leaderboard!",
                color=0xffd700
            )
            # Still show level requirements
            level_info = "**Level Requirements:**\n"
            for level, xp in LEVEL_THRESHOLDS.items():
                level_info += f"Level {level}: {xp:,} XP\n"
            embed.add_field(name="Level System", value=level_info, inline=False)
            await interaction.response.send_message(embed=embed)
            return
        
        embed = discord.Embed(
            title="🏆 XP Leaderboard",
            description="Top 10 Quest Completers",
            color=0xffd700
        )
        
        medals = ["🥇", "🥈", "🥉"]
        users_added = 0
        
        for i, (user_id, xp, level) in enumerate(leaderboard_data):
            medal = medals[i] if i < 3 else f"#{i+1}"
            
            # Try multiple methods to get user info
            user = interaction.guild.get_member(user_id)
            if not user:
                user = bot.get_user(user_id)
            
            # Calculate total XP including role-based XP
            total_xp = quest_bot.calculate_total_user_xp(user_id, interaction.guild.id)
            
            if user:
                # Format username without pinging - use @ but escape it
                username = f"@{user.name}"
                display_name = getattr(user, 'display_name', user.name)
                if display_name != user.name:
                    username = f"@{user.name} ({display_name})"
                
                embed.add_field(
                    name=f"{medal} Level {level}",
                    value=f"{username}\n{total_xp:,} XP",
                    inline=True
                )
                users_added += 1
            else:
                # Try to fetch user info from Discord API
                try:
                    user = await bot.fetch_user(user_id)
                    username = f"@{user.name}"
                    embed.add_field(
                        name=f"{medal} Level {level}",
                        value=f"{username}\n{total_xp:,} XP",
                        inline=True
                    )
                    users_added += 1
                except:
                    # Last resort - show user ID
                    embed.add_field(
                        name=f"{medal} Level {level}",
                        value=f"@User{str(user_id)[-4:]}\n{total_xp:,} XP",
                        inline=True
                    )
                    users_added += 1
        
        if users_added == 0:
            embed.add_field(
                name="No Active Users", 
                value="Users with XP may have left the server", 
                inline=False
            )
        
        # Add level requirements info
        level_info = "**Level Requirements:**\n"
        for level, xp_req in LEVEL_THRESHOLDS.items():
            level_info += f"Level {level}: {xp_req:,} XP\n"
        
        embed.add_field(name="Level System", value=level_info, inline=False)
        await interaction.response.send_message(embed=embed)
        
    except Exception as e:
        print(f"Error in slash leaderboard command: {e}")
        await interaction.response.send_message("❌ Could not retrieve leaderboard data. Please try again later.", ephemeral=True)

@bot.tree.command(name="createlevelroles", description="Manually create all level roles (Level 1-10)")
async def slash_create_level_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    await quest_bot.create_level_roles(interaction.guild)
    await interaction.followup.send("✅ Level roles created/verified for Levels 1-10!", ephemeral=True)

@bot.tree.command(name="assignlevelroles", description="Assign level roles to all users based on their current XP")
async def slash_assign_level_roles(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_roles:
        await interaction.response.send_message("❌ You need Manage Roles permission to use this command!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    # Get all users from database
    leaderboard_data = quest_bot.get_leaderboard(interaction.guild.id, 1000)  # Get all users
    
    assigned_count = 0
    for user_id, xp, level in leaderboard_data:
        member = interaction.guild.get_member(user_id)
        if member:
            # Remove any existing level roles
            level_roles = [role for role in member.roles if role.name.startswith("Level ")]
            if level_roles:
                await member.remove_roles(*level_roles, reason="Reassigning level roles")
            
            # Add correct level role
            level_role_name = f"Level {level}"
            level_role = discord.utils.get(interaction.guild.roles, name=level_role_name)
            if level_role:
                await member.add_roles(level_role, reason=f"Assigned {level_role_name} based on XP")
                assigned_count += 1
    
    await interaction.followup.send(f"✅ Assigned level roles to {assigned_count} users based on their current XP!", ephemeral=True)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command!", delete_after=5)
    elif isinstance(error, commands.MissingRole):
        await ctx.send("❌ You need the @staff role to use this command!", delete_after=5)
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ Invalid argument provided!", delete_after=5)
    else:
        await ctx.send("❌ An error occurred while processing the command!", delete_after=5)

if __name__ == "__main__":
    import os
    
    # Get token from environment variable
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    
    if not TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set!")
        print("Please set your Discord bot token as an environment variable.")
        exit(1)

        webserver.keep_alive()
    
    # Run the bot
    bot.run(TOKEN)
