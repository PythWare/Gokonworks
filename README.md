# Update

Gokonworks is updated with support for executable patching and loose file mod support.

# Gokonworks Info

Gokonworks is a modding toolkit for Akiba's Trip Undead & Undressed. The design of Gokonworks is inspired by my favorite anime, How I Attended An All-Guy's Mixer.

Scroll down to see GUI examples of the toolkit if you desire to, make sure to read the readme especially requirements section. Modders/Gamers should read Modding Workflow section

# Requirements

Python 3 and Pillow (installed in command prompt with `python -m pip install pillow`). Pillow is a Python imaging library

After having those 2 requirements met, you should be able to run Gokonworks by double clicking main.pyw. If for some reason it doesn't run (most likely a faulty python installation), open command prompt in the current directory and type `python main.pyw`

# How Gokonworks actaully works

Gokonworks will unpack Akiba's volume.dat archive file completely, creating a taildata json file for storing metadata of the files.

Gokonworks patches the executable to allow loose file accessing, meaning loose file mods can be applied to the game instead of rebuilding/appending/overwriting volume.dat

Gokonworks will backup the volume.dat archive and executable.

# Modding Workflow

For modders that wish to mod the game, you must run the unpack and run the executable patches you wish to use with Patch EXE button. Whenever you mod the unpacked files and want to apply them to the game, you need to place them in the Mods folder. It's an easy process, just store the modded files in a folder hierarchy that matches the unpacked files (i.e., suppose you modded maplist.bin, the folder hierarchy for the mod would be Mods\lang_common\map\maplist.bin).

For gamers who only download/apply mods, you only need to click Pacth EXE button and select the options desired and then place the downloaded mods in the Mods folder.

The game will read the files in the Mods folder after its been patched by Gokonworks.

To disable mods, just delete the mods from the Mods folder.

# Hub Examples

The theme of Gokonworks is as explained above, inspired by my favorite anime but also the concept of Gokon.

When unpacking volume.dat, the Mocktail glass will gradually fill as a visual indicator of the progress

<img width="1120" height="910" alt="ngokon1" src="https://github.com/user-attachments/assets/b50ac01e-9008-4d90-979a-e5e2cb2af39c" />

<img width="1120" height="913" alt="ngokon3" src="https://github.com/user-attachments/assets/d0207129-79a8-416b-b81f-260d4db59c3b" />

<img width="1121" height="913" alt="ngokon4" src="https://github.com/user-attachments/assets/a8cfe7e6-f358-4ab7-9493-abf89bfba693" />

# EXE Patcher

<img width="623" height="509" alt="ngokon5" src="https://github.com/user-attachments/assets/2d118721-f0de-4f79-89a7-921d11647ade" />

<img width="623" height="492" alt="ngokon6" src="https://github.com/user-attachments/assets/3e25057d-9e65-4736-b1f7-969dd79078b1" />

# PNG usage

Gokonworks includes 2 PNG images in the pngs folder (bottle.png and glass.png), I don't own them. bottle.png and glass.png are free images that had a free distribution license that I downloaded to use with the GUI but their license forbids monetizing them. So if you use Gokonworks, you are now informed those assets are not permitted for financial gain.

# Therion info

The Therion_Guide folder holds a txt I wrote for the Therion executable found in volume.dat when unpacked, if you intend to make script mods that mod cutscenes, dialogue flow, altering what triggers a battle, adding new scenes, etc then I suggest reading the text file. The quick rundown is Therion compiles scripts for the game to read, for more details read the text file.
