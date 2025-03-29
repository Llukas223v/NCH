############### IMPORTS ###############
import discord
from discord.ext import commands
from discord import app_commands
import datetime
import json
import os
from dotenv import load_dotenv
import logging
import asyncio
from typing import Dict, List, Optional, Union, Any, Literal
import traceback
import nacl  # Add this for voice support
import aiohttp
import re
from pymongo import MongoClient
import pymongo

# Define intents first
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # If you need member events

# Simple bot setup
bot = commands.Bot(command_prefix="!", intents=intents)

class ItemView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=180)  # 3 minute timeout
        self.category = category
        
        # Add buttons for each item in category
        for item in shop_data.item_categories[category]:
            self.add_item(ItemButton(item))
        
class ItemButton(discord.ui.Button):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.gray,
            custom_id=item_name
        )

    async def callback(self, interaction: discord.Interaction):
        # Pass the parent view to the modal
        modal = QuantityModal(self.internal_name, self.view)
        await interaction.response.send_modal(modal)
        
class RemoveItemButton(discord.ui.Button):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.red,
            custom_id=f"remove_{item_name}"
        )

    async def callback(self, interaction: discord.Interaction):
        modal = RemoveQuantityModal(self.internal_name)
        await interaction.response.send_modal(modal)

# Add this new class after RemoveItemButton:
################### BULK ACTIONS ##########################
class BulkAddModal(discord.ui.Modal, title="Bulk Add Items"):
    items_input = discord.ui.TextInput(
        label="Items (format: item:quantity, item:quantity)",
        style=discord.TextStyle.paragraph,
        placeholder="bud_sojokush:50, joint_sojokush:25...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        items_text = self.items_input.value.strip()
        items_to_add = []
        errors = []
        
        # Parse the input - more flexible formatting
        for item_entry in re.split(r'[,\n]', items_text):
            item_entry = item_entry.strip()
            if not item_entry:
                continue
                
            # More flexible pattern matching - accept "item 50" or "item:50" or "50 item"
            match = re.match(r'([a-z_]+)[:\s]+(\d+)', item_entry)
            if not match:
                match = re.match(r'(\d+)[:\s]+([a-z_]+)', item_entry)
                if match:
                    quantity_str, item_name = match.groups()
                else:
                    errors.append(f"Invalid format: `{item_entry}`")
                    continue
            else:
                item_name, quantity_str = match.groups()
                
            try:
                quantity = int(quantity_str)
            except ValueError:
                errors.append(f"Invalid quantity for `{item_name}`: {quantity_str}")
                continue
                
            # Try to find closest match if item doesn't exist exactly
            if not shop_data.is_valid_item(item_name):
                # Try to find a match by partial name
                matches = [i for i in shop_data.get_all_items() if item_name in i]
                if len(matches) == 1:
                    item_name = matches[0]  # Use the match
                else:
                    errors.append(f"Unknown item: `{item_name}`")
                    continue
                
            items_to_add.append((item_name, quantity))
        
        # Process the valid items
        if not items_to_add:
            await interaction.followup.send("❌ No valid items found to add.", ephemeral=True)
            return
            
        user = str(interaction.user)
        total_added = 0
        total_value = 0
        
        # Add all valid items to stock
        for item_name, quantity in items_to_add:
            price = shop_data.predefined_prices.get(item_name, 0)
            value = price * quantity
            
            shop_data.add_item(item_name, quantity, user)
            shop_data.add_to_history("add", item_name, quantity, price, user)
            
            total_added += 1
            total_value += value
        
        # Refresh the stock channel
        try:
            if shop_data.stock_message_id:
                await update_stock_message()
        except Exception as e:
            logger.error(f"❌ Error updating stock message: {e}")
        
        # Send confirmation with errors if any
        confirmation = f"✅ Added {total_added} items worth ${total_value:,} to stock!"
        if errors:
            confirmation += "\n\n⚠️ Errors:\n" + "\n".join(errors)
            
        await interaction.followup.send(confirmation, ephemeral=True)
        
class BulkRemoveModal(discord.ui.Modal, title="Bulk Remove Items"):
    items_input = discord.ui.TextInput(
        label="Items (format: item:quantity, item:quantity)",
        style=discord.TextStyle.paragraph,
        placeholder="bud_sojokush:50, joint_sojokush:25...",
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        items_text = self.items_input.value.strip()
        items_to_remove = []
        errors = []
        
        # Parse the input
        for item_entry in re.split(r'[,\n]', items_text):
            item_entry = item_entry.strip()
            if not item_entry:
                continue
                
            # Parse item:quantity format
            match = re.match(r'([a-z_]+):(\d+)', item_entry)
            if not match:
                errors.append(f"Invalid format: `{item_entry}` (use item_name:quantity)")
                continue
                
            item_name, quantity_str = match.groups()
            try:
                quantity = int(quantity_str)
            except ValueError:
                errors.append(f"Invalid quantity for `{item_name}`: {quantity_str}")
                continue
                
            if not shop_data.is_valid_item(item_name):
                errors.append(f"Unknown item: `{item_name}`")
                continue
                
            # Check if user has enough of the item
            user = str(interaction.user)
            user_quantity = shop_data.get_user_quantity(item_name, user)
            if user_quantity < quantity:
                errors.append(f"Not enough `{item_name}` (you have {user_quantity}, tried to remove {quantity})")
                continue
                
            items_to_remove.append((item_name, quantity))
        
        # Process the valid items
        if not items_to_remove:
            await interaction.followup.send("❌ No valid items found to remove.", ephemeral=True)
            return
            
        user = str(interaction.user)
        total_removed = 0
        total_value = 0
        
        # Remove all valid items from stock
        for item_name, quantity in items_to_remove:
            price = shop_data.predefined_prices.get(item_name, 0)
            value = price * quantity
            
            shop_data.remove_item(item_name, quantity, user)
            shop_data.add_to_history("remove", item_name, quantity, price, user)
            
            total_removed += 1
            total_value += value
        
        # Refresh the stock channel
        try:
            if shop_data.stock_message_id:
                await update_stock_message()
        except Exception as e:
            logger.error(f"❌ Error updating stock message: {e}")
        
        # Send confirmation with errors if any
        confirmation = f"✅ Removed {total_removed} items worth ${total_value:,} from stock!"
        if errors:
            confirmation += "\n\n⚠️ Errors:\n" + "\n".join(errors)
            
        await interaction.followup.send(confirmation, ephemeral=True)   
        
class BulkAddView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=300)
        self.category = category
        self.selected_items = {}
        
        # Add all items from the category
        for item in shop_data.item_categories[category]:
            display_name = shop_data.display_names.get(item, item)
            button = BulkItemSelectButton(item, display_name)
            self.add_item(button)
        
        # Add confirm button at the bottom
        self.add_item(BulkConfirmButton())

class BulkItemSelectButton(discord.ui.Button):
    def __init__(self, item_name: str, display_name: str):
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.gray,
            custom_id=f"bulk_{item_name}"
        )
        self.item_name = item_name
        
    async def callback(self, interaction: discord.Interaction):
        # Show a modal to enter quantity
        modal = BulkQuantityModal(self.item_name, self.view)
        await interaction.response.send_modal(modal)

class BulkQuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str, parent_view):
        self.item_name = item_name
        self.parent_view = parent_view
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name}")
        
        self.quantity = discord.ui.TextInput(
            label="Quantity to add",
            placeholder="Enter amount (e.g. 50)",
            required=True,
            min_length=1,
            max_length=6
        )
        self.add_item(self.quantity)
        
    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity.value)
            if quantity <= 0:
                raise ValueError("Quantity must be positive")
                
            # Store in parent view
            self.parent_view.selected_items[self.item_name] = quantity
            
            # Update UI to show selected items
            items_text = "\n".join([
                f"• {shop_data.display_names.get(item, item)}: {qty}" 
                for item, qty in self.parent_view.selected_items.items() 
                if qty > 0
            ])
            
            if not items_text:
                items_text = "No items selected yet"
                
            embed = discord.Embed(
                title=f"🛒 Bulk Add: {self.parent_view.category.title()}",
                description="Click items to set quantities, then click Confirm when done.",
                color=COLORS['INFO']
            )
            embed.add_field(name="Selected Items", value=items_text)
            
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
            
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid positive number",
                ephemeral=True
            )

class BulkConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Confirm Selection",
            style=discord.ButtonStyle.primary,
            row=4  # Place at bottom
        )
        
    async def callback(self, interaction: discord.Interaction):
        view = self.view
        selected_items = [(item, qty) for item, qty in view.selected_items.items() if qty > 0]
        
        if not selected_items:
            await interaction.response.send_message("❌ No items selected!", ephemeral=True)
            return
            
        # Process the selected items
        user = str(interaction.user)
        total_added = 0
        total_value = 0
        
        for item_name, quantity in selected_items:
            price = shop_data.predefined_prices.get(item_name, 0)
            value = price * quantity
            
            # Add each item to stock
            date = str(datetime.date.today())
            if item_name not in shop_data.items:
                shop_data.items[item_name] = []
                
            shop_data.items[item_name].append({
                "person": user,
                "quantity": quantity,
                "date": date,
                "price": price
            })
            
            shop_data.add_to_history("add", item_name, quantity, price, user)
            
            total_added += quantity
            total_value += value
        
        # Refresh the stock channel
        try:
            if hasattr(shop_data, 'stock_message_id') and shop_data.stock_message_id:
                await update_stock_message()
        except Exception as e:
            logger.error(f"❌ Error updating stock message: {e}")
            
        shop_data.save_data()
        
        # Confirmation
        embed = discord.Embed(
            title="✅ Items Added to Stock",
            description=f"Added {total_added} items worth ${total_value:,} to stock!",
            color=COLORS['SUCCESS']
        )
        
        items_text = "\n".join([f"• {shop_data.display_names.get(item, item)}: {qty}" 
                              for item, qty in selected_items])
        embed.add_field(name="Items", value=items_text)
        
        await interaction.response.edit_message(embed=embed, view=None)
            
                         

############## REMOVE ###################

class RemoveQuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str):
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Remove {display_name}")
        
        self.quantity = discord.ui.TextInput(
            label=f"Amount to remove",
            placeholder="Enter amount to remove",
            required=True
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity.value)
            user = str(interaction.user)
            
            # Check if user has enough
            total_user_quantity = shop_data.get_user_quantity(self.internal_name, user)
            if total_user_quantity < quantity:
                await interaction.response.send_message(
                    f"❌ You only have {total_user_quantity}x {shop_data.display_names.get(self.internal_name)} in stock",
                    ephemeral=True
                )
                return

            # Remove the stock
            removed = 0
            for entry in shop_data.items[self.internal_name]:
                if entry['person'] == user:
                    if entry['quantity'] <= quantity - removed:
                        removed += entry['quantity']
                        entry['quantity'] = 0
                    else:
                        entry['quantity'] -= (quantity - removed)
                        removed = quantity
                        break

            # Clean up empty entries
            shop_data.items[self.internal_name] = [
                entry for entry in shop_data.items[self.internal_name] 
                if entry['quantity'] > 0
            ]

            # Save changes
            shop_data.add_to_history("remove", self.internal_name, quantity, 0, user)
            shop_data.save_data()
            await update_stock_message()

            display_name = shop_data.display_names.get(self.internal_name)
            embed = discord.Embed(
                title="✅ Stock Removed",
                color=COLORS['SUCCESS']
            )
            embed.add_field(
                name="Details",
                value=f"```ml\nItem: {display_name}\nQuantity: {quantity:,}\nRemaining: {shop_data.get_total_quantity(self.internal_name):,}```",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in remove quantity modal: {e}")
            await interaction.response.send_message(
                "❌ Something went wrong. Please try again.",
                ephemeral=True
            )
            
class RemoveCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
    
    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.red, row=0)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.red, row=0)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.red, row=1)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bag')

    @discord.ui.button(label="🎮 Tebex", style=discord.ButtonStyle.red, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'tebex')

    async def show_category_items(self, interaction: discord.Interaction, category: str):
        # Create a new view with buttons for each item
        view = discord.ui.View(timeout=180)
        
        # Only show items the user has in stock
        user = str(interaction.user)
        for item in shop_data.item_categories[category]:
            if shop_data.get_user_quantity(item, user) > 0:
                view.add_item(RemoveItemButton(item))
        
        if not view.children:
            await interaction.response.send_message(
                f"❌ You don't have any {category} items in stock!",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            embed=discord.Embed(
                title=f"🗑️ Remove {category.title()}",
                description="Select an item to remove from your stock:",
                color=COLORS['ERROR']
            ),
            view=view,
            ephemeral=True
        )

# Replace the existing QuantityModal class with:

class QuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str, view_to_return):
        self.internal_name = item_name
        self.view_to_return = view_to_return  # Store the original view
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name}")
        
        self.quantity = discord.ui.TextInput(
            label=f"Amount of {display_name}",
            placeholder="Enter amount to add",
            required=True
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Convert input to integer
            quantity = int(self.quantity.value)
            
            # Process the addition
            success = await add_stock_internal(
                interaction=interaction,
                quantity=quantity,
                item=self.internal_name,
                respond=False  # Don't send a response here
            )
            
            if success:
                # Get item details for the confirmation
                display_name = shop_data.display_names.get(self.internal_name, self.internal_name)
                price = shop_data.predefined_prices.get(self.internal_name, 0)
                
                # Create an updated view with confirmation header
                if isinstance(self.view_to_return, ItemView):
                    # For ItemView, we need to pass the category
                    category = self.view_to_return.category
                    updated_view = ItemView(category)
                else:
                    # For other view classes without required parameters
                    updated_view = self.view_to_return.__class__()
                
                # Add confirmation header
                embed = discord.Embed(
                    title="✅ Added Successfully",
                    description=f"Added {quantity}× {display_name} at ${price} each",
                    color=COLORS['SUCCESS']
                )
                
                try:
                    # Try to edit the original message
                    await interaction.response.edit_message(embed=embed, view=updated_view)
                except discord.errors.InteractionResponded:
                    # Fall back to followup if already responded
                    await interaction.followup.send(
                        embed=embed,
                        ephemeral=True
                    )
        
        except ValueError:
            try:
                await interaction.response.send_message(
                    "❌ Please enter a valid number",
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    "❌ Please enter a valid number",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error in quantity modal: {e}")
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong. Please try again.",
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    "❌ Something went wrong. Please try again.",
                    ephemeral=True
                )
            
class TemplateSelectView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=180)
        self.user_id = user_id
        
        # Add select dropdown
        select = discord.ui.Select(
            placeholder="Choose a template...",
            min_values=1,
            max_values=1
        )
        
        # Populate options
        templates = shop_data.get_user_templates(user_id)
        for name in templates.keys():
            select.add_option(label=name, value=name)
        
        # Set callback
        select.callback = self.select_callback
        self.add_item(select)
    
    async def select_callback(self, interaction: discord.Interaction):
        # Get the selected template name
        template_name = interaction.data['values'][0]
        user = str(interaction.user)
        templates = shop_data.get_user_templates(user)
        
        if template_name not in templates:
            await interaction.response.send_message(
                "❌ Template not found",
                ephemeral=True
            )
            return
        
        # Get template details
        template_items = templates[template_name]
        item_details = []
        total_value = 0
        total_quantity = 0
        
        # Create details list
        for item, quantity in template_items.items():
            display_name = shop_data.display_names.get(item, item)
            price = shop_data.predefined_prices.get(item, 0)
            value = quantity * price
            total_value += value
            total_quantity += quantity
            item_details.append(f"{display_name}: {quantity:,}× (${value:,})")
        
        # Create preview embed
        embed = discord.Embed(
            title=f"Template: {template_name}",
            description=f"This template contains {len(template_items)} different items with {total_quantity:,} total quantity.",
            color=COLORS['INFO']
        )
        
        embed.add_field(
            name="Items in Template",
            value="```ml\n" + "\n".join(item_details) + "```",
            inline=False
        )
        
        embed.add_field(
            name="Total Value",
            value=f"${total_value:,}",
            inline=False
        )
        
        # Create confirmation view
        confirm_view = TemplateConfirmView(template_name)
        
        # Edit the message with template details and confirmation button
        await interaction.response.edit_message(embed=embed, view=confirm_view)

# Add new class for confirmation
class TemplateConfirmView(discord.ui.View):
    def __init__(self, template_name):
        super().__init__(timeout=180)
        self.template_name = template_name
    
    @discord.ui.button(label="✅ Apply Template", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = str(interaction.user)
        template = shop_data.get_user_templates(user).get(self.template_name, {})
        
        if not template:
            await interaction.response.send_message(
                "❌ Template not found or empty",
                ephemeral=True
            )
            return
            
        # Add each item in the template to stock
        added_items = 0
        total_value = 0
        
        embed = discord.Embed(
            title=f"✅ Applied Template: {self.template_name}",
            color=COLORS['SUCCESS']
        )
        
        item_details = []
        
        for item, quantity in template.items():
            if quantity <= 0:
                continue
                
            # Add to stock with default price
            price = shop_data.predefined_prices.get(item, 0)
            value = quantity * price
            total_value += value
                
            # Add item to stock
            date = str(datetime.date.today())
            if item not in shop_data.items:
                shop_data.items[item] = []
                
            shop_data.items[item].append({
                "person": user,
                "quantity": quantity,
                "date": date,
                "price": price
            })
            
            # Record the addition
            shop_data.add_to_history("add", item, quantity, price, user)
            
            # Add to display details
            display_name = shop_data.display_names.get(item, item)
            item_details.append(f"{display_name}: {quantity:,} (${value:,})")
            added_items += 1
        
        if added_items > 0:
            shop_data.save_data()
            await update_stock_message()
            
            embed.description = f"Added {added_items} items worth ${total_value:,}"
            embed.add_field(
                name="Items Added",
                value="```ml\n" + "\n".join(item_details) + "```",
                inline=False
            )
        else:
            embed.title = "❌ Template Error"
            embed.description = "No valid items found in template"
            embed.color = COLORS['ERROR']
        
        await interaction.response.edit_message(embed=embed, view=None)  # Remove buttons after applying
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Template application cancelled.",
            embed=None,
            view=None
        )
            
class TemplateNameModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create Template")
        
        self.template_name = discord.ui.TextInput(
            label="Template Name",
            placeholder="My Daily Restock",
            required=True,
            max_length=32
        )
        self.add_item(self.template_name)
    
    async def on_submit(self, interaction: discord.Interaction):
        template_name = self.template_name.value
        
        # Create empty template and store in current session
        user = str(interaction.user)
        
        # Initialize the visual category view
        template_view = TemplateVisualCategoryView(template_name)
        template_view.user = user
        
        # Create initial embed
        embed = discord.Embed(
            title=f"📋 New Template: {template_name}",
            description="Select categories and add items to your template.",
            color=COLORS['INFO']
        )
        
        embed.add_field(
            name="Instructions",
            value="1. Click on a category button\n"
                  "2. Select items and set quantities\n"
                  "3. Return to this menu with the Back button\n"
                  "4. Click 'Finish Template' when done",
            inline=False
        )
        
        await interaction.response.send_message(
            content=f"Creating template: **{template_name}**",
            embed=embed,
            view=template_view,
            ephemeral=True
        )

# This should appear BEFORE TemplateCategoryView
class TemplateItemButton(discord.ui.Button):
    def __init__(self, template_name: str, item_name: str):
        self.template_name = template_name
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(
            label=display_name,
            style=discord.ButtonStyle.gray,
            custom_id=f"template_{item_name}"
        )

    async def callback(self, interaction: discord.Interaction):
        # Show quantity modal
        modal = TemplateItemQuantityModal(self.template_name, self.internal_name)
        await interaction.response.send_modal(modal)

# This class also needs to be defined before it's used
class TemplateItemQuantityModal(discord.ui.Modal):
    def __init__(self, template_name: str, item_name: str):
        self.template_name = template_name
        self.internal_name = item_name
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name} to Template")
        
        self.quantity = discord.ui.TextInput(
            label=f"Amount of {display_name}",
            placeholder="Enter amount to add",
            required=True
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity.value)
            user = str(interaction.user)
            
            # Get existing template
            if user not in shop_data.user_templates:
                shop_data.user_templates[user] = {}
                
            if self.template_name not in shop_data.user_templates[user]:
                shop_data.user_templates[user][self.template_name] = {}
                
            # Add/update item in template
            shop_data.user_templates[user][self.template_name][self.internal_name] = quantity
            shop_data.save_data()
            
            # Get item details for confirmation
            display_name = shop_data.display_names.get(self.internal_name, self.internal_name)
            price = shop_data.predefined_prices.get(self.internal_name, 0)
            
            # Send confirmation
            embed = discord.Embed(
                title="✅ Item Added to Template",
                color=COLORS['SUCCESS']
            )
            embed.add_field(
                name="Details",
                value=f"```ml\nTemplate: {self.template_name}\nItem: {display_name}\nQuantity: {quantity}\nValue: ${quantity * price:,}```",
                inline=False
            )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error in template item quantity modal: {e}")
            logger.error(traceback.format_exc())  # Add this line to see the full error
            await interaction.response.send_message(
                "❌ Something went wrong. Please try again.",
                ephemeral=True
            )
        
class TemplateCategoryView(discord.ui.View):
    def __init__(self, template_name):
        super().__init__(timeout=180)
        self.template_name = template_name

    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bag')
        
    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'tebex')

    async def show_category_items(self, interaction: discord.Interaction, category: str):
        view = discord.ui.View(timeout=180)
        
        for item in shop_data.item_categories[category]:
            view.add_item(TemplateItemButton(self.template_name, item))
        
        await interaction.response.send_message(
            f"Select items from **{category.title()}** to add to template:",
            view=view,
            ephemeral=True
        )

class TemplateItemView(discord.ui.View):
    def __init__(self, template_name: str, category: str):
        super().__init__(timeout=180)
        self.template_name = template_name
        self.category = category
        
        # Add buttons for each item in category
        for item in shop_data.item_categories[category]:
            button = TemplateItemButton(self.template_name, item)
            self.add_item(button)

class TemplateDeleteView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        
        # Add select dropdown
        self.select = discord.ui.Select(
            placeholder="Choose a template to delete...",
            min_values=1,
            max_values=1
        )
        
        # Callback will be set in setup_for_user method
        self.select.callback = self.select_callback
        self.add_item(self.select)
    
    async def setup_for_user(self, user_id: str):
        """Configure dropdown options for specific user"""
        # Clear existing options
        self.select.options = []
        
        # Populate options for this user
        templates = shop_data.get_user_templates(user_id)
        for name in templates.keys():
            self.select.add_option(label=name, value=name)
        
        return self
    
    async def select_callback(self, interaction: discord.Interaction):
        template_name = interaction.data['values'][0]
        user = str(interaction.user)
        
        # Remove template
        if user in shop_data.user_templates and template_name in shop_data.user_templates[user]:
            del shop_data.user_templates[user][template_name]
            shop_data.save_data()
            
            await interaction.response.send_message(
                f"✅ Template **{template_name}** deleted",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Template not found",
                ephemeral=True
            )

class TemplateVisualCategoryView(discord.ui.View):
    def __init__(self, template_name):
        super().__init__(timeout=180)
        self.template_name = template_name
        self.selected_items = {}  # Track selected items and quantities
        
    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, row=0)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, row=0)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, row=1)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'bag')
        
    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category_items(interaction, 'tebex')
    
    @discord.ui.button(label="✅ Finish Template", style=discord.ButtonStyle.success, row=2)
    async def finish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.save_template(interaction)

    async def show_category_items(self, interaction: discord.Interaction, category: str):
        # First, create updated embed showing current selections
        embed = self.create_current_selection_embed()
    
        # Now create the category items view
        item_view = TemplateVisualItemView(self.template_name, category, self.selected_items)
    
        # Use edit_message instead of send_message to update the current message
        await interaction.response.edit_message(
            content=f"Select items from **{category.title()}** to add to template:",
            embed=embed,
            view=item_view
        )
        
    def create_current_selection_embed(self):
        """Create an embed showing current template selections"""
        embed = discord.Embed(
            title=f"📋 Template: {self.template_name}",
            description="Items selected for this template:",
            color=COLORS['INFO']
        )
        
        # Get the items from the actual template (for existing templates)
        user_templates = shop_data.user_templates.get(self.user, {})
        template = user_templates.get(self.template_name, {})
        
        # Combine with currently selected items
        all_items = {}
        all_items.update(template)  # Start with existing items
        all_items.update(self.selected_items)  # Override with new selections
        
        if not all_items:
            embed.description = "No items selected yet."
            return embed
        
        # Group by category
        by_category = {}
        total_value = 0
        
        for item, qty in all_items.items():
            if qty <= 0:
                continue
                
            category = shop_data.get_category_for_item(item)
            if category not in by_category:
                by_category[category] = []
            
            price = shop_data.predefined_prices.get(item, 0)
            value = qty * price
            total_value += value
            
            display_name = shop_data.display_names.get(item, item)
            by_category[category].append(f"{display_name}: {qty:,} (${value:,})")
        
        # Add each category as a field
        for category, items in by_category.items():
            if not items:
                continue
                
            emoji = CATEGORY_EMOJIS.get(category, '📦')
            embed.add_field(
                name=f"{emoji} {category.title()}",
                value="\n".join(items),
                inline=False
            )
        
        # Show total value
        embed.add_field(
            name="💰 Total Value",
            value=f"${total_value:,}",
            inline=False
        )
        
        return embed
    
    async def save_template(self, interaction: discord.Interaction):
        """Save the template and show confirmation"""
        user = str(interaction.user)
        self.user = user
    
        # Get existing template
        if user not in shop_data.user_templates:
            shop_data.user_templates[user] = {}
    
        # Check if we're editing an existing template or creating new
        is_edit = self.template_name in shop_data.user_templates[user]
    
        # Get the original template (for comparing changes later)
        original_items = {}
        if is_edit:
            original_items = shop_data.user_templates[user][self.template_name].copy()
    
        # Create or update the template
        shop_data.user_templates[user][self.template_name] = {}
        template = shop_data.user_templates[user][self.template_name]
    
        # Apply our selections
        for item, qty in self.selected_items.items():
            if qty > 0:  # Only add positive quantities
                template[item] = qty
    
        shop_data.save_data()
    
        # Create confirmation embed
        embed = self.create_current_selection_embed()
    
        if is_edit:
            embed.title = f"✅ Template Updated: {self.template_name}"
        
            # Show what changed
            added_items = []
            removed_items = []
            modified_items = []
        
            # Find added and modified items
            for item, qty in template.items():
                original_qty = original_items.get(item, 0)
                if original_qty == 0:
                    display_name = shop_data.display_names.get(item, item)
                    added_items.append(f"{display_name}: {qty}")
                elif qty != original_qty:
                    display_name = shop_data.display_names.get(item, item)
                    modified_items.append(f"{display_name}: {original_qty} → {qty}")
        
            # Find removed items
            for item, qty in original_items.items():
                if item not in template:
                    display_name = shop_data.display_names.get(item, item)
                    removed_items.append(f"{display_name}: {qty}")
        
            # Add change summary if there were changes
            if added_items or removed_items or modified_items:
                changes = []
                if added_items:
                    changes.append("**Added:**\n" + "\n".join(f"• {item}" for item in added_items))
                if modified_items:
                    changes.append("**Modified:**\n" + "\n".join(f"• {item}" for item in modified_items))
                if removed_items:
                    changes.append("**Removed:**\n" + "\n".join(f"• {item}" for item in removed_items))
            
                embed.add_field(
                    name="Changes Made",
                    value="\n\n".join(changes),
                    inline=False
                )
        else:
            embed.title = f"✅ Template Saved: {self.template_name}"
    
        await interaction.response.edit_message(
            content="Template saved successfully!",
            embed=embed,
            view=None  # Remove buttons
        )
        
class TemplateVisualItemView(discord.ui.View):
    def __init__(self, template_name, category, selected_items):
        super().__init__(timeout=180)
        self.template_name = template_name
        self.category = category
        self.selected_items = selected_items
        
        # Add buttons for each item in category
        for item in shop_data.item_categories[category]:
            button = TemplateVisualItemButton(item, self.selected_items.get(item, 0))
            self.add_item(button)
            
        # Add a "Back" button to return to main view
        self.add_item(self.create_back_button())
    
    def create_back_button(self):
        """Create a button to go back to category selection"""
        back_button = discord.ui.Button(
            label="↩️ Back to Categories", 
            style=discord.ButtonStyle.secondary,
            row=4
        )
        
        async def back_callback(interaction):
            # Create a new category view with current selections
            category_view = TemplateVisualCategoryView(self.template_name)
            category_view.selected_items = self.selected_items
            
            # Get user for consistent handling
            user = str(interaction.user)
            category_view.user = user
            
            # Update the message with category view
            embed = category_view.create_current_selection_embed()
            
            await interaction.response.edit_message(
                content=f"Creating template: **{self.template_name}**",
                embed=embed,
                view=category_view
            )
        
        back_button.callback = back_callback
        return back_button

class TemplateVisualItemButton(discord.ui.Button):
    def __init__(self, item_name, current_qty=0):
        self.item_name = item_name
        self.current_qty = current_qty
        display_name = shop_data.display_names.get(item_name, item_name)
        
        # Show quantity if already selected
        label = display_name
        if current_qty > 0:
            label = f"{display_name} ({current_qty})"
            
        super().__init__(
            label=label,
            style=discord.ButtonStyle.gray if current_qty == 0 else discord.ButtonStyle.green,
            custom_id=f"template_item_{item_name}"
        )

    async def callback(self, interaction: discord.Interaction):
        # Show modal to set quantity
        modal = TemplateVisualQuantityModal(self.item_name, self.view)
        await interaction.response.send_modal(modal)
        
class TemplateVisualQuantityModal(discord.ui.Modal):
    def __init__(self, item_name, parent_view):
        self.item_name = item_name
        self.parent_view = parent_view
        display_name = shop_data.display_names.get(item_name, item_name)
        super().__init__(title=f"Add {display_name} to Template")
        
        # Show current quantity as default value if it exists
        current_qty = parent_view.selected_items.get(item_name, 0)
        
        self.quantity = discord.ui.TextInput(
            label=f"Quantity",
            placeholder="Enter amount (0 to remove)",
            required=True,
            default=str(current_qty) if current_qty > 0 else ""
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            quantity = int(self.quantity.value)
            if quantity < 0:
                raise ValueError("Quantity cannot be negative")
                
            # Update the parent view's selected items
            self.parent_view.selected_items[self.item_name] = quantity
            
            # Update the button appearance
            for item in self.parent_view.children:
                if isinstance(item, TemplateVisualItemButton) and item.item_name == self.item_name:
                    display_name = shop_data.display_names.get(self.item_name, self.item_name)
                    item.label = f"{display_name} ({quantity})" if quantity > 0 else display_name
                    item.style = discord.ButtonStyle.green if quantity > 0 else discord.ButtonStyle.gray
                    break
            
            # Create main category view with updated selections
            main_view = TemplateVisualCategoryView(self.parent_view.template_name)
            main_view.selected_items = self.parent_view.selected_items
            
            # Create an updated item selection view
            new_item_view = TemplateVisualItemView(
                self.parent_view.template_name,
                self.parent_view.category,
                self.parent_view.selected_items
            )
            
            # Create embed showing current selections
            embed = discord.Embed(
                title=f"Item Added: {shop_data.display_names.get(self.item_name, self.item_name)}",
                description=f"Quantity set to: **{quantity}**",
                color=COLORS['SUCCESS']
            )
            
            # Add summary of current selections
            current_items = []
            for item, qty in self.parent_view.selected_items.items():
                if qty > 0:
                    display_name = shop_data.display_names.get(item, item)
                    price = shop_data.predefined_prices.get(item, 0) * qty
                    current_items.append(f"{display_name}: {qty:,} (${price:,})")
            
            if current_items:
                embed.add_field(
                    name="Current Selections",
                    value="\n".join(current_items),
                    inline=False
                )
            
            await interaction.response.edit_message(
                content=f"Select items from **{self.parent_view.category.title()}** to add to template:",
                embed=embed,
                view=new_item_view
            )
            
        except ValueError:
            await interaction.response.send_message(
                "❌ Please enter a valid number (0 or positive)",
                ephemeral=True
            )
            
                            

# Setup logging with more detailed formatting
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("discord_bot")

####################################################################################################################################

############### CONSTANTS ###############
COLORS = {
    'SUCCESS': 0x57F287,  # Green
    'ERROR': 0xED4245,    # Red
    'INFO': 0x3498DB,     # Blue
    'WARNING': 0xFEE75C,  # Yellow
    'DEFAULT': 0x2F3136   # Dark
}

DATA_FILE = "data.json"
CONFIG_FILE = "config.json"
CATEGORY_EMOJIS = {
    'bud': '🥦',
    'joint': '🚬',
    'bag': '🛍️',
    'tebex': '💎'
}


LOW_STOCK_THRESHOLDS = {
    'bud': 30,
    'joint': 100,
    'bag': 100,
    'tebex': 10
}

# Load environment variables
load_dotenv('.env')
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    logger.critical("❌ BOT_TOKEN not found in environment variables!")
    raise ValueError("BOT_TOKEN environment variable is required")

STOCK_CHANNEL_ID = int(os.getenv('STOCK_CHANNEL_ID', 0))
if not STOCK_CHANNEL_ID:
    logger.warning("⚠️ STOCK_CHANNEL_ID not set or invalid. Stock updates will be disabled.")
    
############### CLASSES ###############

class CategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)  # 5 minute timeout
    
    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green, row=0)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🥦 Add Buds",
                description="Select an item to add to your stock:",
                color=COLORS['SUCCESS']
            ),
            view=ItemView('bud'),
            ephemeral=True
        )

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple, row=0)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🚬 Add Joints",
                description="Select an item to add to your stock:",
                color=COLORS['INFO']
            ),
            view=ItemView('joint'),
            ephemeral=True
        )

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray, row=1)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🛍️ Add Bags",
                description="Select an item to add to your stock:",
                color=COLORS['DEFAULT']
            ),
            view=ItemView('bag'),
            ephemeral=True
        )

    @discord.ui.button(label="🎮 Tebex", style=discord.ButtonStyle.primary, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🎮 Add Tebex Items",
                description="Select an item to add to your stock:",
                color=COLORS['INFO']
            ),
            view=ItemView('tebex'),
            ephemeral=True
        )
    
# Predefined data structures
class ShopData:
    def __init__(self):
        self.items: Dict[str, List[Dict[str, Any]]] = {}
        self.user_earnings: Dict[str, int] = {}
        self.sale_history: List[Dict[str, Any]] = []
        self.stock_message_id: Optional[int] = None
        self.user_templates: Dict[str, Dict[str, Dict[str, int]]] = {}
        self.user_preferences: Dict[str, Dict[str, Any]] = {}
    
    # Add MongoDB connection
        self.using_mongodb = False
        mongo_uri = os.getenv("MONGO_URI")
        if mongo_uri:
            try:
                logger.info("🔌 Connecting to MongoDB...")
                self.mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
                # Test connection
                self.mongo_client.server_info()
                self.db = self.mongo_client["shop_bot"]
                self.using_mongodb = True
                logger.info("✅ Connected to MongoDB successfully")
            except pymongo.errors.ServerSelectionTimeoutError as e:
                logger.error(f"❌ MongoDB connection failed: {e}")
                logger.info("💾 Falling back to local JSON storage")
            except Exception as e:
                logger.error(f"❌ MongoDB error: {e}")
                logger.info("💾 Falling back to local JSON storage")
        else:
            logger.info("💾 Using local JSON storage (no MONGO_URI provided)")
        
        self.display_names = {
            'bud_sojokush': 'Bizarre Bud',
            'bud_khalifakush': 'Strange Bud',
            'bud_pineappleexpress': 'Smelly Bud',
            'bud_sourdiesel': 'Sour Diesel Bud',
            'bud_whitewidow': 'Whacky Bud',
            'bud_ogkush': 'Old Bud',
            'joint_sojokush': 'Bizarre Joint',
            'joint_khalifakush': 'Strange Joint',
            'joint_pineappleexpress': 'Smelly Joint',
            'joint_sourdiesel': 'Sour Diesel Joint',
            'joint_whitewidow': 'Whacky Joint',
            'joint_ogkush': 'Old Joint',
            'bagof_sojokush': 'Bizarre Bag',
            'bagof_khalifakush': 'Strange Bag',
            'bagof_pineappleexpress': 'Smelly Bag',
            'bagof_sourdiesel': 'Sour Diesel Bag',
            'bagof_whitewidow': 'Whacky Bag',
            'bagof_ogkush': 'Old Bag'
        }
        self.display_names.update({
            'tebex_vinplate': 'Stolen Plate',
            'tebex_talentreset': 'Talent Reset',
            'tebex_deep_pockets': 'Deep Pockets',
            'licenseplate': 'Custom Plate',
            'tebex_carwax': 'Car Wax',
            'tebex_xpbooster': 'XP Booster'
        })
    
        # Predefined prices and categories
        self.predefined_prices = {
            'bud_sojokush': 4950,
            'bud_khalifakush': 1095,
            'bud_pineappleexpress': 735,
            'bud_sourdiesel': 735,
            'bud_whitewidow': 645,
            'bud_ogkush': 630,
            'joint_ogkush': 28,
            'joint_whitewidow': 28,
            'joint_sourdiesel': 30,
            'joint_pineappleexpress': 35,
            'joint_khalifakush': 60,
            'joint_sojokush': 130,
            'bagof_ogkush': 35,
            'bagof_whitewidow': 35,
            'bagof_sourdiesel': 35,
            'bagof_pineappleexpress': 38,
            'bagof_khalifakush': 69,
            'bagof_sojokush': 300
        }
        self.predefined_prices.update({
            'tebex_vinplate': 350000,
            'tebex_talentreset': 550000,
            'tebex_deep_pockets': 1075000,
            'licenseplate': 535000,
            'tebex_carwax': 595000,
            'tebex_xpbooster': 1450000
        })
        
        self.item_categories = {
            'bud': ['bud_ogkush', 'bud_whitewidow', 'bud_sourdiesel', 'bud_pineappleexpress', 
                   'bud_khalifakush', 'bud_sojokush'],
            'joint': ['joint_ogkush', 'joint_whitewidow', 'joint_sourdiesel', 'joint_pineappleexpress', 
                     'joint_khalifakush', 'joint_sojokush'],
            'bag': ['bagof_ogkush', 'bagof_whitewidow', 'bagof_sourdiesel', 'bagof_pineappleexpress', 
                   'bagof_khalifakush', 'bagof_sojokush']
        }
        self.item_categories['tebex'] = [
            'tebex_vinplate',           # Added commas
            'tebex_talentreset',
            'tebex_deep_pockets',
            'licenseplate',
            'tebex_carwax',
            'tebex_xpbooster'
        ]
        # Create a list of all valid items
        self.item_list = list(self.predefined_prices.keys())
    
    def save_data(self) -> None:
        if self.using_mongodb:
            try:
                # Save items collection
                for item_name, entries in self.items.items():
                    self.db.items.update_one(
                        {"_id": item_name},
                        {"$set": {"entries": entries}},
                        upsert=True
                    )
            
            # Save settings
                self.db.settings.update_one(
                    {"_id": "predefined_prices"},
                    {"$set": {"data": self.predefined_prices}},
                    upsert=True
                )
                self.db.settings.update_one(
                    {"_id": "user_earnings"},
                    {"$set": {"data": self.user_earnings}},
                    upsert=True
                )
                self.db.settings.update_one(
                    {"_id": "user_templates"},
                    {"$set": {"data": self.user_templates}},
                    upsert=True
                )
                self.db.settings.update_one(
                    {"_id": "user_preferences"},
                    {"$set": {"data": self.user_preferences}},
                    upsert=True
                )
            
                # For sale history, limit to recent entries
                recent_history = self.sale_history[-500:]  # Keep last 500 entries
                self.db.settings.update_one(
                    {"_id": "sale_history"},
                    {"$set": {"data": recent_history}},
                    upsert=True
                )
            
                logger.info("💾 Data saved to MongoDB")
            except Exception as e:
                logger.error(f"❌ MongoDB save error: {e}")
                logger.error(traceback.format_exc())
                # Fall back to local storage
                self._save_local()
        else:
            # Use local storage
            self._save_local()
        
    def _save_local(self) -> None:
        """Save data to local JSON file"""
        try:
            with open(DATA_FILE, "w") as f:
                json.dump({
                    "items": self.items,
                    "earnings": self.user_earnings,
                    "sale_history": self.sale_history,
                    "user_templates": self.user_templates,
                    "predefined_prices": self.predefined_prices,
                    "user_preferences": self.user_preferences
                }, f, indent=2)
            logger.info("💾 Data saved to data.json")
        except Exception as e:
            logger.error(f"❌ Error saving data: {e}")
    def load_data(self) -> None:
        if self.using_mongodb:
            try:
                # Load items
                for item_doc in self.db.items.find():
                    if "entries" in item_doc:
                        self.items[item_doc["_id"]] = item_doc["entries"]
            
                # Load settings
                prices_doc = self.db.settings.find_one({"_id": "predefined_prices"})
                if prices_doc and "data" in prices_doc:
                    self.predefined_prices.update(prices_doc["data"])
            
                earnings_doc = self.db.settings.find_one({"_id": "user_earnings"})
                if earnings_doc and "data" in earnings_doc:
                    self.user_earnings.update(earnings_doc["data"])
                
                templates_doc = self.db.settings.find_one({"_id": "user_templates"})
                if templates_doc and "data" in templates_doc:
                    self.user_templates.update(templates_doc["data"])
                
                prefs_doc = self.db.settings.find_one({"_id": "user_preferences"})
                if prefs_doc and "data" in prefs_doc:
                    self.user_preferences.update(prefs_doc["data"])
                
                history_doc = self.db.settings.find_one({"_id": "sale_history"})
                if history_doc and "data" in history_doc:
                 self.sale_history = history_doc["data"]
            
                logger.info("📂 Data loaded from MongoDB")
            except Exception as e:
                logger.error(f"❌ MongoDB load error: {e}")
                logger.error(traceback.format_exc())
                # Fall back to local storage
                self._load_local()
        else:
            # Use local storage
            self._load_local()       
    def _load_local(self) -> None:
        """Load data from local JSON file"""
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                if "predefined_prices" in data:
                    logger.info(f"Found saved prices: {data['predefined_prices']}")
                    old_prices = self.predefined_prices.copy()
                    self.predefined_prices.update(data["predefined_prices"])
                    logger.info(f"Updated prices from: {old_prices}")
                    logger.info(f"Updated prices to: {self.predefined_prices}")            
                self.items.update(data.get("items", {}))
                self.user_earnings.update(data.get("earnings", {}))
                if "sale_history" in data:
                    self.sale_history.extend(data["sale_history"])
                if "user_templates" in data:
                    self.user_templates.update(data["user_templates"])
                if "predefined_prices" in data:
                    self.predefined_prices.update(data["predefined_prices"])
                if "user_preferences" in data:
                    self.user_preferences.update(data["user_preferences"])
            logger.info("📂 Data loaded from local file")
        except FileNotFoundError:
            logger.info("📝 No existing data found. Starting fresh.")
        except json.JSONDecodeError:
            logger.error("❌ Corrupted data file. Starting fresh.")
        except Exception as e:
            logger.error(f"❌ Error loading local data: {e}")
            logger.error(traceback.format_exc())
    
    def load_config(self) -> None:
        """Load configuration from config.json"""
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                self.stock_message_id = config.get("stock_message_id")
                logger.info(f"📂 Config loaded: stock_message_id={self.stock_message_id}")
        except FileNotFoundError:
            logger.info("📝 No existing config found. Starting fresh.")
        except Exception as e:
            logger.error(f"❌ Error loading config: {e}")
    
    def save_config(self) -> None:
        """Save configuration to config.json"""
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"stock_message_id": self.stock_message_id}, f, indent=2)
            logger.info("💾 Config saved")
        except Exception as e:
            logger.error(f"❌ Error saving config: {e}")
    
    def get_total_quantity(self, item_name: str) -> int:
        """Get total quantity of an item in stock"""
        if item_name not in self.items:
            return 0
        return sum(entry['quantity'] for entry in self.items[item_name])
    
    def get_user_quantity(self, item_name: str, user: str) -> int:
        """Get quantity of an item owned by a specific user"""
        if item_name not in self.items:
            return 0
        return sum(entry['quantity'] for entry in self.items[item_name] if entry['person'] == user)
    
    def get_all_items(self):
        """Return all valid item names"""
        return self.item_list

    def add_item(self, item_name: str, quantity: int, user: str) -> bool:
        """Add an item to stock"""
        price = self.predefined_prices.get(item_name, 0)
        date = str(datetime.date.today())
    
        if item_name not in self.items:
            self.items[item_name] = []
    
        self.items[item_name].append({
            "person": user,
            "quantity": quantity,
            "date": date,
            "price": price
        })
    
        return True

    def remove_item(self, item_name: str, quantity: int, user: str) -> bool:
        """Remove an item from stock"""
        if item_name not in self.items:
            return False
        
        # Remove the stock
        removed = 0
        for entry in self.items[item_name]:
            if entry['person'] == user:
                if entry['quantity'] <= quantity - removed:
                    removed += entry['quantity']
                    entry['quantity'] = 0
                else:
                    entry['quantity'] -= (quantity - removed)
                    removed = quantity
                    break

        # Clean up empty entries
        self.items[item_name] = [
            entry for entry in self.items[item_name] 
            if entry['quantity'] > 0
        ]
    
        return removed == quantity
    
    def is_valid_item(self, item_name: str) -> bool:
        """Check if an item is valid"""
        return item_name in self.predefined_prices
    
    def add_to_history(self, action: str, item: str, quantity: int, price: int, user: str) -> None:
        """Add an event to the sale history with more details"""
        self.sale_history.append({
            "date": str(datetime.datetime.now()),
            "action": action,
            "item": item,
            "quantity": quantity,
            "price": price,
            "user": user
        })
    
    def get_category_for_item(self, item_name: str) -> Optional[str]:
        """Get category for an item"""
        for category, items in self.item_categories.items():
            if item_name in items:
                return category
        return None

    def is_low_stock(self, item_name: str, quantity: int) -> bool:
        """Check if item is low on stock"""
        category = self.get_category_for_item(item_name)
        if not category:
            return False
        return quantity <= LOW_STOCK_THRESHOLDS.get(category, 0)
    def save_template(self, user: str, template_name: str, items: Dict[str, int]) -> bool:
        if user not in self.user_templates:
            self.user_templates[user] = {}
        
        self.user_templates[user][template_name] = items
        self.save_data()
        return True

    def get_user_templates(self, user: str) -> Dict[str, Dict[str, int]]:
        return self.user_templates.get(user, {})
    def get_user_preference(self, user: str, preference: str, default: Any = None) -> Any:
        """Get a user preference with fallback to default"""
        if user not in self.user_preferences:
            return default
        return self.user_preferences[user].get(preference, default)
    def set_user_preference(self, user: str, preference: str, value: Any) -> None:
        """Set a user preference"""
        if user not in self.user_preferences:
            self.user_preferences[user] = {}
        self.user_preferences[user][preference] = value
        self.save_data()  # Save preferences immediately
        
    
shop_data = ShopData()

# Add this class near your other View classes
class StockView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)  # 3 minute timeout

    @discord.ui.button(label="🥦 Buds", style=discord.ButtonStyle.green)
    async def buds_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'bud')

    @discord.ui.button(label="🚬 Joints", style=discord.ButtonStyle.blurple)
    async def joints_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'joint')

    @discord.ui.button(label="🛍️ Bags", style=discord.ButtonStyle.gray)
    async def bags_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'bag')
        
    @discord.ui.button(label="💎 Tebex", style=discord.ButtonStyle.primary, row=1)
    async def tebex_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'tebex')

    @discord.ui.button(label="📊 All Stock", style=discord.ButtonStyle.primary)
    async def all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.show_category(interaction, 'all')

    async def show_category(self, interaction: discord.Interaction, category: str):
        user = str(interaction.user)
        compact_mode = shop_data.get_user_preference(user, "compact_view", False)
    
        embed = discord.Embed(
            title="📊 Current Stock",
            color=COLORS['INFO'],
            timestamp=datetime.datetime.now()
        )
    
        total_value = 0
        categories = shop_data.item_categories.items()
        if category != 'all':
            categories = [(category, shop_data.item_categories[category])]

        for cat, category_items in categories:
            content = []
            category_value = 0
        
            for item_name in category_items:
                qty = shop_data.get_total_quantity(item_name)
                if qty > 0:
                    price = shop_data.predefined_prices.get(item_name, 0)
                    value = qty * price
                    category_value += value
                    display_name = shop_data.display_names.get(item_name, item_name)
                
                    # Different formats based on mode
                    if compact_mode:
                        # Compact mode - just essentials with minimal formatting
                        status = "⚠️" if qty <= LOW_STOCK_THRESHOLDS.get(cat, 0) else ""
                        content.append(f"{display_name}: {qty:,} (${value:,}) {status}")
                    else:
                    # Standard mode - formatted with alignment
                        status = ""
                        if qty <= LOW_STOCK_THRESHOLDS.get(cat, 0): status = "⚠️ LOW"
                        elif qty >= LOW_STOCK_THRESHOLDS.get(cat, 0) * 3: status = "📈 HIGH"
                        else: status = "✅ OK"
                    
                        content.append(f"`{display_name[:15]:<15} {qty:>4,} @ ${price:>6,} = ${value:>8,} {status}`")
        
            if content:
                total_value += category_value
            
                if compact_mode:
                    # More concise category header in compact mode
                    name = f"{CATEGORY_EMOJIS.get(cat, '📦')} {cat.upper()}: ${category_value:,}"
                    value = "\n".join(content)
                else:
                    name = f"{CATEGORY_EMOJIS.get(cat, '📦')} {cat.upper()} (${category_value:,})"
                    value = "\n".join(content)
            
                embed.add_field(name=name, value=value, inline=False)
    
        if total_value > 0:
            embed.description = f"💰 Total Value: ${total_value:,}"
        else:
            embed.description = "No items in stock!"
    
    # Add toggle button
        view = StockViewToggle(compact_mode)
    
        embed.set_footer(text=f"{'Compact' if compact_mode else 'Standard'} View • Use /add or /quickadd to add items")
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
class StockViewToggle(discord.ui.View):
    def __init__(self, current_compact_mode: bool):
        super().__init__(timeout=180)
        self.compact_mode = current_compact_mode

    @discord.ui.button(label="Toggle View Mode", style=discord.ButtonStyle.secondary)
    async def toggle_view(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = str(interaction.user)
        new_mode = not self.compact_mode
        shop_data.set_user_preference(user, "compact_view", new_mode)
        
        # Create new stock view with updated preference
        stock_view = StockView()
        await stock_view.show_category(interaction, 'all')  # Show all categories

class TemplateEditSelectView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=180)
        self.user_id = user_id
        
        # Add select dropdown
        select = discord.ui.Select(
            placeholder="Choose a template to edit...",
            min_values=1,
            max_values=1
        )
        
        # Populate options
        templates = shop_data.get_user_templates(user_id)
        for name in templates.keys():
            select.add_option(label=name, value=name)
        
        # Set callback
        select.callback = self.select_callback
        self.add_item(select)
    
    async def select_callback(self, interaction: discord.Interaction):
        # Get the selected template name
        template_name = interaction.data['values'][0]
        user = str(interaction.user)
        templates = shop_data.get_user_templates(user)
        
        if template_name not in templates:
            await interaction.response.send_message(
                "❌ Template not found",
                ephemeral=True
            )
            return
        
        # Initialize the visual category view for editing
        template_view = TemplateVisualCategoryView(template_name)
        template_view.user = user
        
        # Load the existing template items to the view
        template_items = templates[template_name]
        template_view.selected_items = template_items.copy()  # Start with existing items
        
        # Create embed showing existing items
        embed = template_view.create_current_selection_embed()
        embed.title = f"📝 Editing Template: {template_name}"
        embed.description = "Make changes to your template by selecting categories and modifying items."
        
        embed.add_field(
            name="Instructions",
            value="1. Click on a category button\n"
                  "2. Add or modify items and quantities\n"
                  "3. Return with the Back button\n"
                  "4. Click 'Finish Template' when done",
            inline=False
        )
        
        # Edit the message with the template editor view
        await interaction.response.edit_message(
            content=f"Editing template: **{template_name}**",
            embed=embed,
            view=template_view
        )           
             

############### HELPER FUNCTIONS ###############
async def is_admin(interaction: discord.Interaction) -> bool:
    """Check if user has admin permissions with debug logging"""
    if not interaction.guild:
        logger.warning(f"is_admin check failed: No guild in interaction for {interaction.user}")
        return False
    
    has_admin = interaction.user.guild_permissions.administrator
    logger.info(f"Admin check for {interaction.user}: {has_admin}")
    return has_admin

async def update_stock_message() -> None:
    """Update the stock tracking message"""
    if not STOCK_CHANNEL_ID:
        logger.error("❌ Stock channel ID not set!")
        return

    channel = bot.get_channel(STOCK_CHANNEL_ID)
    if not channel:
        logger.error(f"❌ Cannot find stock channel {STOCK_CHANNEL_ID}")
        return

    messages = []  # We'll split content into multiple messages if needed
    current_message = "**📊 Current Shop Stock**\n\n"
    current_message += f"Last updated: <t:{int(datetime.datetime.now().timestamp())}:R>\n\n"
    total_value = 0

    for category, category_items in shop_data.item_categories.items():
        category_content = f"{CATEGORY_EMOJIS.get(category, '')} **{category.upper()}**\n```ml\n"
        category_content += f"{'Item':<20} {'Stock':>8} {'Price':>8} {'Value':>10}\n"
        category_content += "─" * 50 + "\n"
        
        has_items = False
        category_value = 0
        
        for item_name in category_items:
            if item_name in shop_data.items:
                total_quantity = shop_data.get_total_quantity(item_name)
                if total_quantity > 0:
                    has_items = True
                    price = shop_data.predefined_prices.get(item_name, 'N/A')
                    item_value = total_quantity * price if price != 'N/A' else 0
                    category_value += item_value
                    display_name = shop_data.display_names.get(item_name, item_name)
                    
                    warning = ""
                    if total_quantity <= LOW_STOCK_THRESHOLDS.get(category, 0):
                        warning = "⚠️"
                    elif total_quantity >= LOW_STOCK_THRESHOLDS.get(category, 0) * 3:
                        warning = "📈"
                    
                    item_line = f"{display_name[:20]:<20} {total_quantity:>8,} {price:>8,} {item_value:>10,} {warning}\n"
                    
                    # Check if adding this line would exceed Discord's limit
                    if len(category_content) + len(item_line) > 1000:
                        category_content += "```\n"
                        if len(current_message + category_content) > 1900:
                            messages.append(current_message)
                            current_message = category_content
                        else:
                            current_message += category_content
                        category_content = f"```ml\n{item_line}"
                    else:
                        category_content += item_line

        if not has_items:
            category_content += "No stock available\n"
        
        category_content += "```\n"
        total_value += category_value
        
        # Check if adding category would exceed limit
        if len(current_message + category_content) > 1900:
            messages.append(current_message)
            current_message = category_content
        else:
            current_message += category_content

    # Add final summary
    summary = f"\n💰 **Total Stock Value:** ${total_value:,}"
    current_message += summary
    messages.append(current_message)

    try:
        # Delete old message(s)
        if shop_data.stock_message_id:
            try:
                old_message = await channel.fetch_message(shop_data.stock_message_id)
                await old_message.delete()
            except discord.NotFound:
                pass

        # Send new message(s)
        last_message = None
        for i, content in enumerate(messages):
            last_message = await channel.send(content)
            if i == len(messages) - 1:  # Save ID of last message
                shop_data.stock_message_id = last_message.id
                shop_data.save_config()
        
        logger.info("📝 Stock message updated")
    except Exception as e:
        logger.error(f"❌ Failed to update stock message: {e}")
        logger.error(traceback.format_exc())
    
async def process_sale(item_name: str, quantity: int, price: int) -> bool:
    """Process a sale from the shop, returns success status"""
    display_name = shop_data.display_names.get(item_name, item_name)
    logger.info(f"🛒 PROCESSING SALE: {quantity}x {display_name}")
    
    if item_name not in shop_data.items:
        logger.error(f"❌ Sale failed: {display_name} not found in stock")
        return False
        
    total_quantity = shop_data.get_total_quantity(item_name)
    
    if total_quantity < quantity:
        logger.error(f"❌ Sale failed: Not enough stock (needed: {quantity}, have: {total_quantity})")
        return False

    remaining = quantity
    # Sort by date (FIFO)
    sorted_entries = sorted(shop_data.items[item_name], key=lambda x: x["date"])
    
    for entry in sorted_entries:
        if remaining <= 0:
            break
            
        sell_amount = min(entry["quantity"], remaining)
        remaining -= sell_amount
        
        logger.info(f"📦 Taking {sell_amount} from {entry['person']}'s stock of {entry['quantity']}")
        
        entry["quantity"] -= sell_amount
        
        if entry["person"] not in shop_data.user_earnings:
            shop_data.user_earnings[entry["person"]] = 0
            
        sale_value = sell_amount * entry["price"]
        shop_data.user_earnings[entry["person"]] += sale_value
        logger.info(f"💰 Added ${sale_value} to {entry['person']}'s earnings")

    # Remove empty entries
    shop_data.items[item_name] = [entry for entry in shop_data.items[item_name] if entry["quantity"] > 0]
    
    # Record the sale
    shop_data.add_to_history("sale", item_name, quantity, price, "customer")
    
    shop_data.save_data()
    await update_stock_message()
    logger.info(f"✅ Sale completed: {quantity}x {display_name}")
    return True

async def item_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """Autocomplete for item names"""
    return [
        app_commands.Choice(name=shop_data.display_names.get(item, item), value=item)
        for item in shop_data.item_list if current.lower() in shop_data.display_names.get(item, item).lower()
    ][:25]

async def add_stock_internal(interaction: discord.Interaction, quantity: int, item: str, price: Optional[int] = None, respond: bool = True) -> bool:
    """Internal function to add stock, used by both /add command and quick add"""
    normalized_item = item.lower().replace(' ', '_')
    
    # Validate item
    if not shop_data.is_valid_item(normalized_item):
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items are: {', '.join(shop_data.predefined_prices.keys())}",
            ephemeral=True
        )
        return False
    
    # Get price
    if price is None:
        price = shop_data.predefined_prices.get(normalized_item)
    
    # Validate quantity
    if quantity <= 0:
        await interaction.response.send_message("❌ Quantity must be positive!", ephemeral=True)
        return False
    if quantity > 100:  # Large quantity threshold
        return await add_large_quantity(interaction, quantity, normalized_item, price)
    
    # Add to stock
    date = str(datetime.date.today())
    if normalized_item not in shop_data.items:
        shop_data.items[normalized_item] = []
        
    shop_data.items[normalized_item].append({
        "person": str(interaction.user),
        "quantity": quantity,
        "date": date,
        "price": price
    })
    
    # Record the addition
    shop_data.add_to_history("add", normalized_item, quantity, price, str(interaction.user))
    
    shop_data.save_data()
    await update_stock_message()
    
    # Only send a response if requested
    if respond:
        display_name = shop_data.display_names.get(normalized_item, normalized_item)
        embed = discord.Embed(
            title="✅ Stock Added",
            description=f"Added {quantity}x {display_name} at ${price} each",
            color=COLORS['SUCCESS']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    return True
    
async def add_large_quantity(interaction: discord.Interaction, quantity: int, normalized_item: str, price: int):
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    embed = discord.Embed(
        title="⚠️ Large Transaction",
        description=f"You're about to add {quantity}x {display_name}. Are you sure?",
        color=COLORS['WARNING']
    )
    
    # Add to stock
    date = str(datetime.date.today())
    if normalized_item not in shop_data.items:
        shop_data.items[normalized_item] = []
        
    shop_data.items[normalized_item].append({
        "person": str(interaction.user),
        "quantity": quantity,
        "date": date,
        "price": price
    })
    
    # Record the addition
    shop_data.add_to_history("add", normalized_item, quantity, price, str(interaction.user))
    
    shop_data.save_data()
    await update_stock_message()
    
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    embed = discord.Embed(
        title="✅ Stock Added",
        description=f"Added {quantity}x {display_name} at ${price} each",
        color=discord.Color.green()
    )
    
    total_quantity = shop_data.get_total_quantity(normalized_item)
    category = next((cat for cat, items in shop_data.item_categories.items() if normalized_item in items), None)
    if category and total_quantity > LOW_STOCK_THRESHOLDS.get(category, 0) * 3:
        embed.add_field(
            name="⚠️ High Stock Warning",
            value=f"You now have {total_quantity} in stock, which is quite high!",
            inline=False
            
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    return True

async def migrate_local_to_mongodb():
    """Migrate data from local JSON to MongoDB"""
    try:
        # Check if local data exists
        if os.path.exists(DATA_FILE):
            logger.info("🔄 Migrating local data to MongoDB...")
            
            # Load local data
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            
            # Connect to MongoDB if not already connected
            if not shop_data.using_mongodb:
                mongo_uri = os.getenv("MONGO_URI")
                if not mongo_uri:
                    logger.error("❌ No MongoDB URI provided for migration")
                    return False
                    
                shop_data.mongo_client = MongoClient(mongo_uri)
                shop_data.db = shop_data.mongo_client["shop_bot"]
                shop_data.using_mongodb = True
            
            # Import items
            if "items" in data:
                for item_name, entries in data["items"].items():
                    shop_data.db.items.update_one(
                        {"_id": item_name},
                        {"$set": {"entries": entries}},
                        upsert=True
                    )
                    
            # Import settings
            if "predefined_prices" in data:
                shop_data.db.settings.update_one(
                    {"_id": "predefined_prices"},
                    {"$set": {"data": data["predefined_prices"]}},
                    upsert=True
                )
                
            if "earnings" in data:
                shop_data.db.settings.update_one(
                    {"_id": "user_earnings"},
                    {"$set": {"data": data["earnings"]}},
                    upsert=True
                )
                
            if "user_templates" in data:
                shop_data.db.settings.update_one(
                    {"_id": "user_templates"},
                    {"$set": {"data": data["user_templates"]}},
                    upsert=True
                )
                
            if "user_preferences" in data:
                shop_data.db.settings.update_one(
                    {"_id": "user_preferences"},
                    {"$set": {"data": data["user_preferences"]}},
                    upsert=True
                )
                
            if "sale_history" in data:
                shop_data.db.settings.update_one(
                    {"_id": "sale_history"},
                    {"$set": {"data": data["sale_history"]}},
                    upsert=True
                )
                
            logger.info("✅ Local data successfully migrated to MongoDB")
            return True
        else:
            logger.info("⚠️ No local data file found for migration")
            return False
    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        logger.error(traceback.format_exc())
        return False

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # If you need member events

bot = commands.Bot(command_prefix="!", intents=intents)

############### COMMANDS ###############

@bot.tree.command(name="help")
async def help_cmd(interaction: discord.Interaction):
    """Show available commands"""
    is_admin_user = await is_admin(interaction)
    
    embed = discord.Embed(
        title="🏪 Shop Bot Commands",
        description="Here's everything you can do with this shop management bot:",
        color=COLORS['INFO']
    )
    
    # Stock Management Commands
    stock_commands = [
        "`/quickadd` - Add items using category buttons",
        "`/add` - Add items to your stock (with quantity)",
        "`/stock` - View current inventory by category",
        "`/quickremove` - Remove items using buttons",
        "`/remove` - Remove items from your stock (with quantity)",
        "`/bulkadd2` - Add multiple items visually",
        "`/bulkremove` - Remove multiple items at once"
    ]
    
    embed.add_field(
        name="📦 Stock Management",
        value="\n".join(stock_commands),
        inline=False
    )
    
    # Template System Commands
    template_commands = [
        "`/template create` - Create a new template",
        "`/template use` - Apply a saved template to add items",
        "`/template list` - View your saved templates",
        "`/template delete` - Delete a template"
    ]
    
    embed.add_field(
        name="📋 Template System",
        value="\n".join(template_commands),
        inline=False
    )
    
    # Financial Commands
    finance_commands = [
        "`/earnings` - Check your current earnings",
        "`/payout` - Cash out your earnings"
    ]
    
    embed.add_field(
        name="💰 Financial",
        value="\n".join(finance_commands),
        inline=False
    )
    
    # Admin Commands (only shown to admins)
    if is_admin_user:
        admin_commands = [
            "`/setstock` - Set exact quantity for any user",
            "`/clearstock` - Clear stock for specific items/users",
            "`/sellmanual` - Process a sale manually",
            "`/price` - Change an item's price",
            "`/userinfo` - View detailed info about any user",
            "`/history` - View transaction history",
            "`/analytics` - View shop analytics and trends",
            "`/backup` - Create a backup of shop data"
        ]
        
        embed.add_field(
            name="⚙️ Admin Commands",
            value="\n".join(admin_commands),
            inline=False
        )
    
    embed.add_field(
        name="💡 Tips",
        value="• Use tab completion for item names\n"
              "• Templates are great for regular restocking\n"
              "• `/stock` shows low inventory warnings\n"
              "• MongoDB integration ensures your data is safe",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="stock")
async def stock_cmd(interaction: discord.Interaction):
    """View current stock levels"""
    await interaction.response.send_message(
        "**📊 Stock Viewer**\nSelect a category to view:",
        view=StockView(),
        ephemeral=True
    )
@bot.tree.command(name="earnings")
async def check_earnings(interaction: discord.Interaction):
    """Check your earnings"""
    user = str(interaction.user)
    earnings = shop_data.user_earnings.get(user, 0)
    
    embed = discord.Embed(
        title="💰 Your Earnings",
        color=discord.Color.gold()
    )
    
    if earnings > 0:
        embed.description = f"Your current earnings: **${earnings:,}**"
    else:
        embed.description = "You have no earnings yet."
        embed.add_field(
            name="💡 How to earn",
            value="Add items to your stock using `/add` and they'll be sold automatically",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="add")
@app_commands.describe(
    quantity="Amount to add",
    item="Item name",
    user="User to add stock for (admin only)",
    price="Price per item (optional, uses default if not specified)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def add_stock(
    interaction: discord.Interaction, 
    quantity: int, 
    item: str,
    user: Optional[discord.Member] = None,
    price: Optional[int] = None
):
    """Add items to stock"""
    # Check permissions if adding for another user
    if user and not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can add stock for other users",
            ephemeral=True
        )
        return

    # Use target user if specified, otherwise use command invoker
    target_user = str(user) if user else str(interaction.user)
    
    normalized_item = item.lower().replace(' ', '_')
    
    # Validate item
    if not shop_data.is_valid_item(normalized_item):
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items are: {', '.join(shop_data.predefined_prices.keys())}",
            ephemeral=True
        )
        return False
    
    # Get price
    if price is None:
        price = shop_data.predefined_prices.get(normalized_item)
    
    # Validate quantity
    if quantity <= 0:
        await interaction.response.send_message("❌ Quantity must be positive!", ephemeral=True)
        return False
    
    # Add to stock
    date = str(datetime.date.today())
    if normalized_item not in shop_data.items:
        shop_data.items[normalized_item] = []
        
    shop_data.items[normalized_item].append({
        "person": target_user,
        "quantity": quantity,
        "date": date,
        "price": price
    })
    
    # Record the addition
    shop_data.add_to_history("add", normalized_item, quantity, price, target_user)
    
    shop_data.save_data()
    await update_stock_message()
    
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    embed = discord.Embed(
        title="✅ Stock Added",
        color=COLORS['SUCCESS']
    )
    embed.add_field(
        name="Details",
        value=f"```ml\nItem: {display_name}\nQuantity: {quantity:,}\nPrice: ${price:,}\nUser: {target_user}\nTotal Value: ${quantity * price:,}```",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    return True

@bot.tree.command(name="remove")
@app_commands.describe(
    quantity="Amount to remove",
    item="Item name"
)
@app_commands.autocomplete(item=item_autocomplete)  # Add autocomplete
async def remove_stock(interaction: discord.Interaction, quantity: int, item: str):
    """Remove items from your stock"""
    # Get internal name from display name
    normalized_item = item.lower().replace(' ', '_')
    user = str(interaction.user)
    
    # Add display name for messages
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    
    # Check if item exists in stock
    if normalized_item not in shop_data.items:
        await interaction.response.send_message(
            f"❌ No stock found for {display_name}",
            ephemeral=True
        )
        return

    # Check if user has enough
    total_user_quantity = shop_data.get_user_quantity(normalized_item, user)
    if total_user_quantity < quantity:
        await interaction.response.send_message(
            f"❌ You only have {total_user_quantity}x {display_name} in stock",
            ephemeral=True
        )
        return

    # Remove the stock
    removed = 0
    for entry in shop_data.items[normalized_item]:
        if entry['person'] == user:
            if entry['quantity'] <= quantity - removed:
                removed += entry['quantity']
                entry['quantity'] = 0
            else:
                entry['quantity'] -= (quantity - removed)
                removed = quantity
                break

    # Clean up empty entries
    shop_data.items[normalized_item] = [
        entry for entry in shop_data.items[normalized_item] 
        if entry['quantity'] > 0
    ]

    # Save changes
    shop_data.add_to_history("remove", normalized_item, quantity, 0, user)
    shop_data.save_data()
    await update_stock_message()
    
    embed = discord.Embed(
        title="✅ Stock Removed",
        color=COLORS['SUCCESS']
    )
    embed.add_field(
        name="Details",
        value=f"```ml\nItem: {display_name}\nQuantity: {quantity:,}\nRemaining: {shop_data.get_total_quantity(normalized_item):,}```",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="setstock")
@app_commands.describe(
    quantity="New quantity to set",
    item="Item name",
    user="User to set stock for (admin only)",
    price="Price per item (optional)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def set_stock(
    interaction: discord.Interaction, 
    quantity: int, 
    item: str,
    user: Optional[discord.Member] = None,
    price: Optional[int] = None
):
    """Set stock quantity for an item (overwrites existing)"""
    # Validate user
    if not interaction.guild:
        await interaction.response.send_message(
            "❌ This command can only be used in a server",
            ephemeral=True
        )
        return

    normalized_item = item.lower().replace(' ', '_')
    target_user = str(user) if user else str(interaction.user)
    
    # Check permissions if setting for another user
    if user and not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can set stock for other users",
            ephemeral=True
        )
        return

    # Validate item
    if not shop_data.is_valid_item(normalized_item):
        valid_items = ", ".join(shop_data.display_names.values())
        await interaction.response.send_message(
            f"❌ Invalid item: {item}\nValid items:\n{valid_items}",
            ephemeral=True
        )
        return

    # Validate quantity
    if quantity < 0:
        await interaction.response.send_message(
            "❌ Quantity cannot be negative",
            ephemeral=True
        )
        return

    try:
        # Get price
        if price is None:
            price = shop_data.predefined_prices.get(normalized_item)
            if price is None:
                await interaction.response.send_message(
                    "❌ No default price found for this item",
                    ephemeral=True
                )
                return

        # Get previous quantity for change tracking
        previous_quantity = shop_data.get_user_quantity(normalized_item, target_user)

        # Remove existing entries for this user and item
        if normalized_item in shop_data.items:
            shop_data.items[normalized_item] = [
                entry for entry in shop_data.items[normalized_item] 
                if entry['person'] != target_user
            ]
        else:
            shop_data.items[normalized_item] = []

        # Add new entry if quantity > 0
        if quantity > 0:
            shop_data.items[normalized_item].append({
                "person": target_user,
                "quantity": quantity,
                "date": str(datetime.date.today()),
                "price": price
            })

        # Record the change
        shop_data.add_to_history("set", normalized_item, quantity, price, target_user)
        shop_data.save_data()
        await update_stock_message()

        # Create response embed
        display_name = shop_data.display_names.get(normalized_item, normalized_item)
        embed = discord.Embed(
            title="✅ Stock Updated",
            description=f"Set {display_name} stock to {quantity} at ${price:,} each",
            color=discord.Color.blue()
        )

        embed.add_field(
            name="Details",
            value=f"**User:** {target_user}\n**Change:** {previous_quantity} → {quantity}",
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    except Exception as e:
        logger.error(f"Error in set_stock: {str(e)}")
        await interaction.response.send_message(
            "❌ An error occurred while updating stock",
            ephemeral=True
        )

@bot.tree.command(name="clearstock")
@app_commands.describe(
    item="Item to clear (leave empty to clear all)",
    user="User to clear stock for (admin only, leave empty to clear all users)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def clear_stock(
    interaction: discord.Interaction,
    item: Optional[str] = None,
    user: Optional[discord.Member] = None
):
    """Clear stock entries (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can clear stock",
            ephemeral=True
        )
        return
    
    target_user = str(user) if user else None
    
    embed = discord.Embed(
        title="🗑️ Stock Cleared",
        color=discord.Color.red()
    )
    
    if item:
        # Clear specific item
        normalized_item = item.lower().replace(' ', '_')
        display_name = shop_data.display_names.get(normalized_item, normalized_item)
        
        if normalized_item in shop_data.items:
            if target_user:
                # Clear for specific user
                shop_data.items[normalized_item] = [
                    entry for entry in shop_data.items[normalized_item] 
                    if entry['person'] != target_user
                ]
                embed.description = f"Cleared {display_name} stock for user {target_user}"
            else:
                # Clear for all users
                shop_data.items[normalized_item] = []
                embed.description = f"Cleared all {display_name} stock"
        else:
            embed.description = f"❌ Item {display_name} not found"
            embed.color = discord.Color.orange()
    else:
        # Clear all items
        if target_user:
            # Clear all items for specific user
            for item_name in shop_data.items:
                shop_data.items[item_name] = [
                    entry for entry in shop_data.items[item_name] 
                    if entry['person'] != target_user
                ]
            embed.description = f"Cleared all stock for user {target_user}"
        else:
            # Clear everything
            shop_data.items.clear()
            embed.description = "⚠️ Cleared all stock for all users"
    
    # Record the clear action
    shop_data.add_to_history(
        "clear", 
        item if item else "all", 
        0, 
        0, 
        target_user if target_user else "all"
    )
    
    shop_data.save_data()
    await update_stock_message()
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Add new command
@bot.tree.command(name="quickadd")
async def quick_add(interaction: discord.Interaction):
    """Quick add multiple items using buttons"""
    await interaction.response.send_message(
        "**📦 Quick Stock Addition**\nSelect a category:",
        view=CategoryView(),
        ephemeral=True
    )

@bot.tree.command(name="history")
@app_commands.describe(
    limit="Number of entries to show (default 10)"
)
async def view_history(
    interaction: discord.Interaction,
    limit: Optional[int] = 10
):
    """View recent sale history (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can view history",
            ephemeral=True
        )
        return
    
    embed = discord.Embed(
        title="📜 Transaction History",
        color=discord.Color.blue(),
        timestamp=datetime.datetime.now()
    )
    
    if not shop_data.sale_history:
        embed.description = "No history available"
    else:
        recent_history = shop_data.sale_history[-limit:]
        
        for i, entry in enumerate(reversed(recent_history), 1):
            action = entry.get("action", "sale")
            date = entry.get("date", "Unknown")
            item = entry.get("item", "Unknown")
            display_name = shop_data.display_names.get(item, item)  # Get display name
            quantity = entry.get("quantity", 0)
            price = entry.get("price", 0)
            user = entry.get("user", "Unknown")
            
            entry_text = f"**Item:** {display_name}\n"  # Use display name
            entry_text += f"**Quantity:** {quantity}\n"
            entry_text += f"**Price:** ${price}\n"
            
            if action != "sale":
                entry_text += f"**User:** {user}\n"
                
            entry_text += f"**Date:** {date[:16]}"
            
            embed.add_field(
                name=f"{i}. {action.capitalize()}", 
                value=entry_text, 
                inline=False
            )
    
    embed.set_footer(text=f"Showing {min(limit, len(shop_data.sale_history))} of {len(shop_data.sale_history)} entries")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)
    
    
@bot.tree.command(name="sellmanual")
@app_commands.describe(
    quantity="Amount sold",
    item="Item name",
    price="Override price (optional, uses default if not set)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def manual_sell(
    interaction: discord.Interaction,
    quantity: int,
    item: str,
    price: Optional[int] = None
):
    """Manually process a sale (admin only)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can manually process sales",
            ephemeral=True
        )
        return

    normalized_item = item.lower().replace(' ', '_')
    display_name = shop_data.display_names.get(normalized_item, normalized_item)
    
    # Get current stock
    current_stock = shop_data.get_total_quantity(normalized_item)
    
    # Use default price if none provided
    if price is None:
        price = shop_data.predefined_prices.get(normalized_item)
        if price is None:
            await interaction.response.send_message(
                "❌ No price specified and no default price found",
                ephemeral=True
            )
            return

    # Process the sale
    success = await process_sale(normalized_item, quantity, price)
    
    if success:
        embed = discord.Embed(
            title="✅ Manual Sale Processed",
            color=discord.Color.green()
        )
        embed.add_field(
            name="Sale Details",
            value=f"Item: {display_name}\n"
                  f"Quantity: {quantity}\n"
                  f"Price: ${price} each\n"
                  f"Total Value: ${quantity * price:,}"
        )
        embed.add_field(
            name="Stock Changes",
            value=f"Previous Stock: {current_stock}\n"
                  f"New Stock: {current_stock - quantity}",
            inline=False
        )
    else:
        embed = discord.Embed(
            title="❌ Sale Failed",
            description="Not enough stock or invalid item",
            color=discord.Color.red()
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)
 
 # Add this new command before the EVENT HANDLERS section:

@bot.tree.command(name="quickremove")
async def quick_remove(interaction: discord.Interaction):
    """Quick remove items using buttons"""
    await interaction.response.send_message(
        "**🗑️ Quick Stock Removal**\nSelect a category:",
        view=RemoveCategoryView(),
        ephemeral=True
    )
    
@bot.tree.command(name="userinfo")
@app_commands.describe(
    user="User to check (leave empty for self)"
)
async def user_info(
    interaction: discord.Interaction,
    user: Optional[discord.Member] = None
):
    """View user's stock and earnings (admin command)"""
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can view user information",
            ephemeral=True
        )
        return

    target_user = str(user) if user else str(interaction.user)
    
    embed = discord.Embed(
        title=f"👤 User Information: {target_user}",
        color=COLORS['INFO'],
        timestamp=datetime.datetime.now()
    )

    # Get earnings
    earnings = shop_data.user_earnings.get(target_user, 0)
    embed.add_field(
        name="💰 Total Earnings",
        value=f"${earnings:,}",
        inline=False
    )

    # Get stock information
    stock_text = ""
    total_value = 0
    
    for category, items in shop_data.item_categories.items():
        category_items = []
        category_value = 0
        
        for item in items:
            quantity = sum(
                entry['quantity'] 
                for entry in shop_data.items.get(item, [])
                if entry['person'] == target_user
            )
            if quantity > 0:
                price = shop_data.predefined_prices.get(item, 0)
                value = quantity * price
                category_value += value
                total_value += value
                display_name = shop_data.display_names.get(item, item)
                category_items.append(f"{display_name}: {quantity:,} (${value:,})")
        
        if category_items:
            stock_text += f"\n{CATEGORY_EMOJIS.get(category, '📦')} **{category.upper()}** - ${category_value:,}\n"
            stock_text += "\n".join(f"└ {item}" for item in category_items)
            stock_text += "\n"

    if stock_text:
        embed.add_field(
            name="📊 Current Stock",
            value=stock_text,
            inline=False
        )
        embed.add_field(
            name="💎 Total Stock Value",
            value=f"${total_value:,}",
            inline=False
        )
    else:
        embed.add_field(
            name="📊 Current Stock",
            value="No items in stock",
            inline=False
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)   
    
@bot.tree.command(name="payout")
@app_commands.describe(
    amount="Amount to cash out (use 'all' for full balance)"
)
async def payout(
    interaction: discord.Interaction,
    amount: str
):
    """Cash out your earnings"""
    user = str(interaction.user)
    current_balance = shop_data.user_earnings.get(user, 0)
    
    if current_balance <= 0:
        embed = discord.Embed(
            title="❌ No Earnings",
            description="You don't have any earnings to cash out.",
            color=COLORS['ERROR']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    try:
        # Handle 'all' amount
        if amount.lower() == 'all':
            payout_amount = current_balance
        else:
            # Convert amount to integer
            payout_amount = int(amount.replace(',', ''))
            
        # Validate amount
        if payout_amount <= 0:
            raise ValueError("Amount must be positive")
        
        if payout_amount > current_balance:
            embed = discord.Embed(
                title="❌ Insufficient Balance",
                description=f"You only have ${current_balance:,} available to cash out.",
                color=COLORS['ERROR']
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        # Process payout
        shop_data.user_earnings[user] -= payout_amount
        
        # Add to history
        shop_data.add_to_history("payout", "earnings", payout_amount, 0, user)
        shop_data.save_data()
        
        # Create response embed
        embed = discord.Embed(
            title="💰 Payout Processed",
            color=COLORS['SUCCESS']
        )
        embed.add_field(
            name="Details",
            value=f"```ml\nAmount: ${payout_amount:,}\nRemaining Balance: ${shop_data.user_earnings[user]:,}```",
            inline=False
        )
        
        # Notify admins if it's a large payout
        if payout_amount >= 1000000:  # Adjust threshold as needed
            for guild_member in interaction.guild.members:
                if guild_member.guild_permissions.administrator:
                    try:
                        admin_embed = discord.Embed(
                            title="💰 Large Payout Alert",
                            description=f"User {user} has cashed out ${payout_amount:,}",
                            color=COLORS['WARNING']
                        )
                        await guild_member.send(embed=admin_embed)
                    except:
                        pass  # Ignore if we can't DM the admin
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"💰 Payout processed for {user}: ${payout_amount:,}")
        
    except ValueError:
        embed = discord.Embed(
            title="❌ Invalid Amount",
            description="Please enter a valid number or 'all'.",
            color=COLORS['ERROR']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)    
        
@bot.tree.command(name="template")
@app_commands.describe(
    action="Action to perform (create, use, list, delete, edit)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="use", value="use"),
    app_commands.Choice(name="list", value="list"),
    app_commands.Choice(name="delete", value="delete"),
    app_commands.Choice(name="edit", value="edit")  # New option
])
async def template_command(interaction: discord.Interaction, action: str):
    user = str(interaction.user)
    
    if action == "create":
        # Show template name modal - this now triggers the visual interface
        await interaction.response.send_modal(TemplateNameModal())
        
    elif action == "use":
        # Get user's templates
        templates = shop_data.get_user_templates(user)
        if not templates:
            await interaction.response.send_message(
                "❌ You don't have any templates. Create one with `/template create`",
                ephemeral=True
            )
            return
            
        # Show template selection - pass the user ID
        await interaction.response.send_message(
            "Select a template to use:",
            view=TemplateSelectView(user),
            ephemeral=True
        )
        
    elif action == "list":
        # List user's templates
        templates = shop_data.get_user_templates(user)
        if not templates:
            await interaction.response.send_message(
                "❌ You don't have any templates. Create one with `/template create`",
                ephemeral=True
            )
            return
            
        # Show templates in embed
        embed = discord.Embed(
            title="📋 Your Templates",
            color=COLORS['INFO']
        )
        
        for name, items in templates.items():
            total_items = len(items)
            total_quantity = sum(items.values())
            template_value = sum(qty * shop_data.predefined_prices.get(item, 0) for item, qty in items.items())
            
            embed.add_field(
                name=name,
                value=f"Items: {total_items}\nQuantity: {total_quantity:,}\nValue: ${template_value:,}",
                inline=True
            )
            
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
    elif action == "delete":
        # Show templates to delete
        templates = shop_data.get_user_templates(user)
        if not templates:
            await interaction.response.send_message(
                "❌ You don't have any templates. Create one with `/template create`",
                ephemeral=True
            )
            return
        
        # Create view and set up for this user
        view = TemplateDeleteView()
        await view.setup_for_user(user)
        
        await interaction.response.send_message(
            "Select a template to delete:",
            view=view,
            ephemeral=True
        )
    
    elif action == "edit":
        # Show templates to edit
        templates = shop_data.get_user_templates(user)
        if not templates:
            await interaction.response.send_message(
                "❌ You don't have any templates. Create one with `/template create`",
                ephemeral=True
            )
            return
        
        # Create view to select a template to edit
        view = TemplateEditSelectView(user)
        
        await interaction.response.send_message(
            "Select a template to edit:",
            view=view,
            ephemeral=True
        )
        
    else:
        await interaction.response.send_message(
            "❌ Invalid action. Use create, use, list, delete, or edit.",
            ephemeral=True
        )
  
@bot.tree.command(name="price")
@app_commands.describe(
    item="Item to change price for",
    new_price="New price for the item",
    update_existing="Whether to update price of existing stock (default: False)"
)
@app_commands.autocomplete(item=item_autocomplete)
async def change_price(
    interaction: discord.Interaction,
    item: str,
    new_price: int,
    update_existing: bool = False
):
    """Change the price of an item (admin only)"""
    # Verify admin permissions
    if not await is_admin(interaction):
        await interaction.response.send_message(
            "❌ Only administrators can change prices",
            ephemeral=True
        )
        return
    
    # Get the internal name from display name if needed
    internal_name = item.lower().replace(' ', '_')
    
    # Check if item exists
    if not shop_data.is_valid_item(internal_name):
        await interaction.response.send_message(
            f"❌ Item not found: {item}",
            ephemeral=True
        )
        return
        
    # Validate the new price
    if new_price <= 0:
        await interaction.response.send_message(
            "❌ Price must be positive",
            ephemeral=True
        )
        return
    
    display_name = shop_data.display_names.get(internal_name, internal_name)
    old_price = shop_data.predefined_prices.get(internal_name, 0)
    
    # Update the predefined price
    shop_data.predefined_prices[internal_name] = new_price
    
    # Update existing stock if requested
    updated_items = 0
    if update_existing and internal_name in shop_data.items:
        for entry in shop_data.items[internal_name]:
            entry["price"] = new_price
            updated_items += entry["quantity"]
    
    # Save changes
    shop_data.save_data()
    
    # Update stock message without waiting
    asyncio.create_task(update_stock_message())
    
    # Build response embed
    embed = discord.Embed(
        title="✅ Price Updated",
        color=COLORS['SUCCESS']
    )
    
    embed.add_field(
        name="Item",
        value=display_name,
        inline=True
    )
    
    embed.add_field(
        name="Price Change",
        value=f"${old_price:,} → ${new_price:,}",
        inline=True
    )
    
    if update_existing:
        embed.add_field(
            name="Stock Updated",
            value=f"Updated price for {updated_items:,} existing items",
            inline=False
        )
    else:
        embed.add_field(
            name="Note",
            value="Only future stock will use this price. Existing items are unchanged.",
            inline=False
        )
    
    # Record in history for tracking
    shop_data.add_to_history(
        "price_change", 
        internal_name,
        0,  # Not a quantity change
        new_price, 
        str(interaction.user)
    )
    
    # Respond to the interaction
    await interaction.response.send_message(embed=embed, ephemeral=True)
            
@bot.tree.command(name="analytics")
async def analytics(interaction: discord.Interaction):
    """View shop analytics and trends"""
    # First respond to the interaction to prevent timeout
    await interaction.response.defer(ephemeral=True)
    
    if not await is_admin(interaction):
        await interaction.followup.send("❌ Admin only", ephemeral=True)
        return
    
    try:
        embed = discord.Embed(title="📊 Shop Analytics", color=COLORS['INFO'])
        
        # Total inventory value
        total_value = 0
        for item, entries in shop_data.items.items():
            qty = sum(entry['quantity'] for entry in entries)
            price = shop_data.predefined_prices.get(item, 0)
            total_value += qty * price
        
        # Sales data
        sales = [entry for entry in shop_data.sale_history if entry.get("action") == "sale"]
        recent_sales = sales[-30:] if len(sales) > 30 else sales
        sales_volume = sum(entry.get("quantity", 0) for entry in recent_sales)
        revenue = sum(entry.get("quantity", 0) * entry.get("price", 0) for entry in recent_sales)
        
        # Stats
        embed.add_field(name="Total Inventory Value", value=f"${total_value:,}", inline=True)
        embed.add_field(name="Recent Sales Volume", value=f"{sales_volume:,} items", inline=True)
        embed.add_field(name="Recent Revenue", value=f"${revenue:,}", inline=True)
        
        # Top items
        item_counts = {}
        for entry in recent_sales:
            item = entry.get("item")
            if item:
                item_counts[item] = item_counts.get(item, 0) + entry.get("quantity", 0)
        
        top_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        if top_items:
            embed.add_field(
                name="Top Selling Items",
                value="\n".join(f"{shop_data.display_names.get(item, item)}: {count:,}" for item, count in top_items),
                inline=False
            )
        
        await interaction.followup.send(embed=embed, ephemeral=True)
        
    except Exception as e:
        # Log detailed error information
        error_details = traceback.format_exc()
        logger.error(f"Analytics command failed: {e}")
        logger.error(error_details)
        await interaction.followup.send(
            f"❌ Error generating analytics: {str(e)}",
            ephemeral=True
        )
        
@bot.tree.command(name="backup")
async def backup_data(interaction: discord.Interaction):
    """Create a backup of shop data"""
    # First respond to the interaction to prevent timeout
    await interaction.response.defer(ephemeral=True)
    
    if not await is_admin(interaction):
        await interaction.followup.send("❌ Admin only", ephemeral=True)
        return
        
    try:
        # Get the absolute path of the current script
        script_path = os.path.abspath(__file__)
        current_dir = os.path.dirname(script_path)
        
        # Create backup filenames
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = os.path.join(current_dir, f"backup_{timestamp}.json")
        data_path = os.path.join(current_dir, DATA_FILE)
        
        # Check if source file exists
        if not os.path.exists(data_path):
            logger.error(f"Data file not found: {data_path}")
            await interaction.followup.send(
                f"❌ Source file '{data_path}' not found",
                ephemeral=True
            )
            return
        
        # Create backup
        logger.info(f"Creating backup from {data_path} to {backup_filename}")
        with open(data_path, "r") as src, open(backup_filename, "w") as dest:
            data = json.load(src)  # Parse JSON to validate it
            json.dump(data, dest, indent=2)  # Write formatted JSON
        
        logger.info(f"Backup created successfully: {backup_filename}")
        await interaction.followup.send(
            f"✅ Backup created: `{backup_filename}`",
            ephemeral=True
        )
    except Exception as e:
        # Log detailed error information
        error_details = traceback.format_exc()
        logger.error(f"Backup failed: {e}")
        logger.error(error_details)
        await interaction.followup.send(
            f"❌ Backup failed: {str(e)}",
            ephemeral=True
        )
                
@bot.tree.command(name="bulkremove", description="Remove multiple items from stock at once")
@app_commands.guild_only()
async def bulk_remove(interaction: discord.Interaction):
    modal = BulkRemoveModal()
    await interaction.response.send_modal(modal)
    
@bot.tree.command(name="bulkadd2", description="Add multiple items to stock at once (visual)")
@app_commands.guild_only()
@app_commands.describe(category="Category of items to add")
async def bulk_add_visual(interaction: discord.Interaction, category: Literal["bud", "joint", "bag", "tebex"]):
    view = BulkAddView(category)
    await interaction.response.send_message(
        f"Select multiple items from **{category}** to add to stock:",
        view=view,
        ephemeral=True
    )



    
############################################################################################################################################                    
############### EVENT HANDLERS ###############
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Check if it's a webhook message
    if message.webhook_id and ("Captain Hook" in message.author.name or True):
        logger.info(f"📨 Webhook message from: {message.author.name}")
        
        # Get message content
        message_text = message.content
        if not message_text and message.embeds:
            embed = message.embeds[0]
            message_text = embed.description or ""
        
        try:
            if "[PURCHASE INFO]" in message_text:
                # Clean and split lines
                lines = [
                    line.strip().replace('**', '').replace('> ', '') 
                    for line in message_text.split('\n') 
                    if line.strip()
                ]
                
                # Parse required info
                item_name = None
                quantity = None
                price = None
                missing = []
                
                for line in lines:
                    if "Name:" in line:
                        item_name = line.split(':', 1)[1].strip()
                    elif "Amount:" in line:
                        try:
                            quantity = int(line.split(':', 1)[1].strip())
                        except ValueError:
                            logger.error(f"❌ Invalid quantity format in line: {line}")
                            missing.append("Valid quantity")
                    elif "Profit:" in line and '$' in line:
                        try:
                            price_text = line.split('$', 1)[1].replace(',', '').strip()
                            price = int(float(price_text))
                        except ValueError:
                            logger.error(f"❌ Invalid price format in line: {line}")
                            missing.append("Valid price")
                
                if not item_name: missing.append("Item name")
                if not quantity: missing.append("Quantity")
                if not price: missing.append("Price")
                
                if missing:
                    logger.error(f"❌ Missing information: {', '.join(missing)}")
                    await message.add_reaction("❓")
                    return
                
                # Get display name for logs
                display_name = shop_data.display_names.get(item_name, item_name)
                logger.info(f"📦 Processing webhook sale: {quantity}x {display_name} for ${price:,}")
                
                success = await process_sale(item_name, quantity, price)
                if success:
                    await message.add_reaction("✅")
                    logger.info(f"✅ Webhook sale completed: {quantity}x {display_name}")
                else:
                    await message.add_reaction("❌")
                    logger.error(f"❌ Webhook sale failed: {quantity}x {display_name}")
                    
        except Exception as e:
            logger.error(f"❌ Error processing webhook: {e}")
            logger.error(traceback.format_exc())
            await message.add_reaction("⚠️")

    await bot.process_commands(message)
    
@bot.event
async def on_ready():
    """Called when the bot is ready"""
    logger.info(f"🤖 Logged in as {bot.user}")
    try:
        # Load saved data
        shop_data.load_config()
        
        # Try to sync commands
        try:
            synced = await bot.tree.sync()
            logger.info(f"✅ Synced {len(synced)} commands")
        except Exception as e:
            logger.error(f"❌ Failed to sync commands: {e}")
            logger.error(traceback.format_exc())
        
        # Update stock display
        try:
            if hasattr(shop_data, 'stock_message_id') and shop_data.stock_message_id:
                await update_stock_message()
        except Exception as e:
            logger.error(f"❌ Failed to update stock message: {e}")
            logger.error(traceback.format_exc())
        
        logger.info("✅ Bot is ready!")
        
    except Exception as e:
        logger.error(f"❌ Error during startup: {e}")
        logger.error(traceback.format_exc())

async def main():
    try:
        # Load shop data before connecting
        shop_data.load_data()
        shop_data.load_config()
        await migrate_local_to_mongodb()

        logger.info("Starting bot...")
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        logger.info("Shutdown requested via keyboard interrupt")
    except asyncio.CancelledError:
        logger.info("Shutdown requested via task cancellation")
    except discord.LoginFailure:
        logger.critical("❌ Invalid token! Please check your TOKEN in .env")
    except Exception as e:
        logger.error(f"Error during bot execution: {e}")
        logger.error(traceback.format_exc())
    finally:
        if not bot.is_closed():
            await bot.close()
        logger.info("Bot has been shut down")
 
# Just before the if __name__ == "__main__" line
logger.info("Script initialized, preparing to connect to Discord...")

if __name__ == "__main__":
    logger.info("Starting async main function...")
    asyncio.run(main())