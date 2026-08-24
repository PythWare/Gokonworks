# Update

Gokonworks is being updated for a new modding workflow. The game can read loose files. So, Gokonworks will soon stop using Mod Creator/Mod Manager infavor of exe patching to make the game load loose file mods.

# Gokonworks Info

Gokonworks is a modding toolkit for Akiba's Trip Undead & Undressed. The design of Gokonworks is inspired by my favorite anime, How I Attended An All-Guy's Mixer.

Gokonworks includes a rad Mod Manager unlike any other visually, the first of its type for Mod Managers. Mods are visualized as wine bottles that are filled when enabled or empty when disabled.

Scroll down to see GUI examples of the toolkit if you desire to, make sure to read the readme especially requirements section. Modders/Gamers should read Modding Workflow section

# Requirements

Python 3 and Pillow (installed in command prompt with `python -m pip install pillow`). Pillow is a Python imaging library, used for the Mod Manager and handling of the GUI of the toolkit for some parts.

After having those 2 requirements met, you should be able to run Gokonworks by double clicking main.pyw. If for some reason it doesn't run (most likely a faulty python installation), open command prompt in the current directory and type `python main.pyw`

# How Gokonworks actaully works

Gokonworks will unpack Akiba's volume.dat archive file completely, creating a taildata json file for storing metadata for proper and safe mod applying/disabling.

Gokonworks appends mods to the end of volume.dat and batch updates TOCs as needed if using Append mode, overwrite mode (used in cases where your mod doesn't change the file size of modded files) overwrites the original file data and updates TOCs if needed. Disabling mods is handled by mod slicing/truncating.

Gokonworks will backup the volume.dat archive to ensure overwrite mode mods are disabled as cleanly as appended mods.

# GUI samples and design explanation

The theme of Gokonworks is as explained above, inspired by my favorite anime but also the concept of Gokon. Mods are visualized as wine bottles on a shelf in the Mod Manager, when disabled they're empty but if enabled the bottle is filled like a wine bottle would be. Also, bottles of mods have the mod's name on the label of the wine bottle.

As more mods are detected, the shelf will expand to allow more room for the growing wine (mod) collection. Use the mousewheel to scroll down as needed if the mod count gets much higher.

When unpacking volume.dat, the Mocktail glass will gradually fill as a visual indicator of the progress

# Modding Workflow

For modders that wish to mod the game, you must run the unpack. Whenever you mod the unpacked files and want to apply them to the game, you need to package them with the Mod Creator. It's an easy process, just store the modded files in a folder hierarchy that matches the unpacked files (i.e., suppose you modded maplist.bin, the folder hierarchy for packaging the mod would be Unpacked_Files\lang_common\map\maplist.bin). Use append mode if your modded files are larger than the original files, use Overwrite mode (done by clicking the Append button toggle) if your mod doesn't change the file size of any original files. 

However, the game by default has a size limit for volume.dat. Volume.dat can only grow to the maximum value a signed int32 has (which is 2,147,483,647), so if your mod requires appending make sure it doesn't exceed 360 MB of data being appended (360 MB is the remaining space vanilla volume.dat has). At a later date i'll make Gokonworks write an executable patch that removes the game's default limit for volume.dat but until then, keep appended mods belong 360 MB in size. If your mod is primarily just changing values and not increasing file sizes, just use overwrite mode.

For gamers who don't want to mod but instead download/apply mods, you only need to unpack the game to create the akiba_taildata.json file. Then place the downloaded .at mods in the Mods folder which is created when you open the Mod Manager within Gokonworks

To apply mods, select the bottle of the mod you want applied and click the Pour button. The log will update but also the Status of the mod will say Poured.

To disable mods, select a bottle that is filled and click the Empty button or Empty Every Bottle button. The difference is Empty reverts the TOC but leaves the appended data, Emprty Every Bottle will revert the TOC and truncate the archive to the original vanilla size.

# Hub Examples

<img width="1114" height="752" alt="fill1" src="https://github.com/user-attachments/assets/b0754680-f127-463f-8fc2-b4bf8c93a55a" />

<img width="1123" height="748" alt="fill2" src="https://github.com/user-attachments/assets/dfeb8a8f-4fe6-43d0-92fc-94915d18f1b5" />

<img width="1121" height="746" alt="fill3" src="https://github.com/user-attachments/assets/cf89b69e-74b8-4f35-9263-8f144f139271" />

# Mod Manager examples

<img width="1916" height="1036" alt="exa5" src="https://github.com/user-attachments/assets/97841974-b059-4b1c-8c48-e8ee63c0a6ac" />

<img width="1239" height="836" alt="exa7" src="https://github.com/user-attachments/assets/e7e01b48-23b6-4582-ac64-4c8fdaaf3dc6" />

# 
Mod Creator example

<img width="759" height="798" alt="gokon1" src="https://github.com/user-attachments/assets/f40df9e3-8d12-4c37-98e9-b13669557e2f" />

<img width="763" height="799" alt="gokon2" src="https://github.com/user-attachments/assets/61b9fa05-b38c-495e-a911-cb1e84ebcfd2" />

# Mod applied/disabled examples

<img width="1281" height="752" alt="exa1" src="https://github.com/user-attachments/assets/7be186d1-64ff-4585-9beb-5247f1d08d69" />

<img width="1283" height="751" alt="exa6" src="https://github.com/user-attachments/assets/fe83204a-9b75-4323-9823-50a732687404" />

# PNG usage

Gokonworks includes 2 PNG images in the pngs folder (bottle.png and glass.png), I don't own them. bottle.png and glass.png are free images that had a free distribution license that I downloaded to use with the GUI but their license forbids monetizing them. So if you use Gokonworks, you are now informed those assets are not permitted for financial gain.

# Therion info

The Therion_Guide folder holds a txt I wrote for the Therion executable found in volume.dat when unpacked, if you intend to make script mods that mod cutscenes, dialogue flow, altering what triggers a battle, adding new scenes, etc then I suggest reading the text file. The quick rundown is Therion compiles scripts for the game to read, for more details read the text file.
