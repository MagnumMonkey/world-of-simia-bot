import discord
from discord import app_commands
from discord.ext import commands

# Change this to wherever you host the forest page (GitHub Pages or your API server)
FOREST_URL = "https://YOURNAME.github.io/YOURREPO/forest/"

class Forest(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="wos_forest", description="Open the Monkey Boy Forest (home).")
    async def wos_forest(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🌲 World of Simia — The Forest Home",
            description=f"Click below to open the forest.\n\n{FOREST_URL}",
        )

        # Optional button (nice UX)
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Open Forest", url=FOREST_URL))

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Forest(bot))