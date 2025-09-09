# Discord Quest Bot

## Overview
A comprehensive Discord bot that manages an XP-based quest system with automatic role tracking and leaderboards. The bot assigns XP points for completed quests and role achievements, with a 10-level progression system.

## Features
- **Quest Management**: Create and manage weekly quests worth 50 XP each
- **XP System**: 10-level progression system with defined thresholds
- **Role-based XP**: Automatic XP for badge roles (5 XP) and streak roles (5 XP)
- **Leaderboard**: Display top users with XP and levels
- **Admin Commands**: Manual XP management and role XP assignment

## Level System
- Level 1: 0 XP
- Level 2: 100 XP
- Level 3: 500 XP
- Level 4: 1,200 XP
- Level 5: 2,200 XP
- Level 6: 3,500 XP
- Level 7: 5,100 XP
- Level 8: 7,000 XP
- Level 9: 9,200 XP
- Level 10: 11,700 XP

## Commands 
**Prefix Commands (using -):**
- `-addquest <title> <content>` - Create new quest embed
- `-removequest <message_id>` - Delete quest by message ID
- `-questping <role_id>` - Set quest ping role
- `-questchannel <channel_id>` - Set quest channel
- `-addXP <member> <amount>` - Add XP to user (staff only)
- `-removeXP <member> <amount>` - Remove XP from user (staff only)
- `-assignroleXP <role> <amount>` - Assign XP value to role
- `-leaderboard` - Display XP rankings
- `-questbot` - Ping bot to check if online

**Slash Commands (using /):**
- `/addquest` - Create new quest embed (with dropdown menus)
- `/removequest` - Delete quest by message ID
- `/questping` - Set quest ping role (with role selector)
- `/questchannel` - Set quest channel (with channel selector)
- `/addxp` - Add XP to user (staff only, with user selector)
- `/removexp` - Remove XP from user (staff only, with user selector)
- `/assignrolexp` - Assign XP value to role (with role selector)
- `/leaderboard` - Display XP rankings
- `/questbot` - Ping bot to check if online

## Technical Details
- Built with discord.py
- SQLite database for persistent data storage
- Automatic reaction tracking for quest completion
- Role change monitoring for XP rewards
- Environment variable for bot token (DISCORD_BOT_TOKEN)

## Current Status
Bot is configured and running. Ready for Discord server integration.