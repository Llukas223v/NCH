EPIC NCH BOT
# NCH Discord Bot
New
## Description
NCH Discord Bot is a comprehensive shop management bot for Discord servers. It allows users to manage their shop inventory, process sales, and handle transactions seamlessly. The bot supports item addition, removal, bulk operations, and template management to keep your shop running smoothly.

## Features
- **Stock Management**: Add, remove, and view stock levels with detailed commands.
- **Bulk Operations**: Add or remove multiple items at once using visual interfaces.
- **Template System**: Create, use, and manage templates for quick restocking.
- **Financial Tracking**: Check earnings and cash out your balance.
- **Admin Controls**: Advanced commands for administrators to manage the shop effectively.

## Installation
### Prerequisites
- Python 3.8+
- Discord.py
- MongoDB (optional for advanced data storage)

### Setup
1. Clone the repository:
    ```bash
    git clone https://github.com/Lukas223v/NCH.git
    cd NCH
    ```

2. Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```

3. Set up environment variables:
    ```bash
    cp .env.example .env
    # Edit the .env file with your configuration
    ```

4. Run the bot:
    ```bash
    python sonnet.py
    ```

## Usage
### Commands
- `/quickadd` - Add items using category buttons
- `/add` - Add items to your stock (with quantity)
- `/stock` - View current inventory by category
- `/quickremove` - Remove items using buttons
- `/remove` - Remove items from your stock (with quantity)
- `/bulkadd2` - Add multiple items visually
- `/bulkremove` - Remove multiple items at once
- `/template create` - Create a new template
- `/template use` - Apply a saved template to add items
- `/template list` - View your saved templates
- `/template delete` - Delete a template
- `/earnings` - Check your current earnings
- `/payout` - Cash out your earnings

### Admin Commands
- `/setstock` - Set exact quantity for any user
- `/clearstock` - Clear stock for specific items/users
- `/sellmanual` - Process a sale manually
- `/price` - Change an item's price
- `/userinfo` - View detailed info about any user
- `/history` - View transaction history
- `/analytics` - View shop analytics and trends
- `/backup` - Create a backup of shop data

## Contributing
1. Fork the repository.
2. Create your feature branch (`git checkout -b feature/YourFeature`).
3. Commit your changes (`git commit -m 'Add some feature'`).
4. Push to the branch (`git push origin feature/YourFeature`).
5. Open a pull request.

## License
This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contact
For support, reach out to [your-email@example.com](mailto:your-email@example.com).
