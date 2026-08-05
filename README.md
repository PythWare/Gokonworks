# Gokonworks Info

Gokonworks is a modding toolkit for Akiba's Trip Undead & Undressed. The design of Gokonworks is inspired by my favorite anime, How I Attended An All-Guy's Mixer.

Gokonworks includes a rad Mod Manager unlike any other visually, the first of its type for Mod Managers. Mods are visualized as wine bottles that are filled when enabled or empty when disabled.

Scroll down to see GUI examples of the toolkit if you desire to, make sure to read the readme especially requirements section. Modders/Gamers should read Modding Workflow section

# Requirements

Python 3 and Pillow (installed in command prompt with `python -m pip install pillow`). Pillow is a Python imaging library, used for the Mod Manager and handling of the GUI of the toolkit for some parts.

After having those 2 requirements met, you should be able to run Gokonworks by double clicking main.pyw. If for some reason it doesn't run (most likely a faulty python installation), open command prompt in the current directory and type `python main.pyw`

# How Gokonworks actaully works

Gokonworks will unpack Akiba's volume.dat archive file completely, creating a taildata json file for storing metadata for proper and safe mod applying/disabling.

Gokonworks appends mods to the end of volume.dat and batch updates TOCs as needed, disabling mods is handled by mod slicing/truncating.

# GUI samples and design explanation

The theme of Gokonworks is as explained above, inspired by my favorite anime but also the concept of Gokon. Mods are visualized as wine bottles on a shelf in the Mod Manager, when disabled they're empty but if enabled the bottle is filled like a wine bottle would be. Also, bottles of mods have the mod's name on the label of the wine bottle.

When unpacking volume.dat, the Mocktail glass will gradually fill as a visual indicator of the progress

# Modding Workflow

For modders that wish to mod the game, you must run the unpack. Whenever you mod the unpacked files and want to apply them to the game, you need to package them with the Mod Creator. It's an easy process, just store the modded files in a folder hierarchy that matches the unpacked files (i.e., suppose you modded maplist.bin, the folder hierarchy for packaging the mod would be Unpacked_Files\lang_common\map\maplist.bin)

For gamers who don't want to mod but instead download/apply mods, you don't need to unpack the game. Just place the downloaded .at mods in the Mods folder which is created when you open the Mod Manager within Gokonworks

To apply mods, select the bottle of the mod you want applied and click the Pour button. The log will update but also the Status of the mod will say Poured.

To disable mods, select a bottle that is filled and click the Empty button or Empty Every Bottle button. The difference is Empty reverts the TOC but leaves the appended data, Emprty Every Bottle will revert the TOC and truncate the archive to the original vanilla size.

# Hub Examples

<img width="1114" height="752" alt="fill1" src="https://github.com/user-attachments/assets/b0754680-f127-463f-8fc2-b4bf8c93a55a" />

<img width="1123" height="748" alt="fill2" src="https://github.com/user-attachments/assets/dfeb8a8f-4fe6-43d0-92fc-94915d18f1b5" />

<img width="1121" height="746" alt="fill3" src="https://github.com/user-attachments/assets/cf89b69e-74b8-4f35-9263-8f144f139271" />

# Mod Manager examples

<img width="1916" height="1036" alt="exa5" src="https://github.com/user-attachments/assets/97841974-b059-4b1c-8c48-e8ee63c0a6ac" />

<img width="1914" height="1034" alt="exa4" src="https://github.com/user-attachments/assets/ab0fb8a6-0da7-42d1-91f0-e07e3c936a3b" />
# 
Mod Creator example

<img width="765" height="812" alt="exa2" src="https://github.com/user-attachments/assets/265b8fb3-5286-4c91-b051-1ea8d670ace6" />

# Mod applied/disabled examples

<img width="1281" height="752" alt="exa1" src="https://github.com/user-attachments/assets/7be186d1-64ff-4585-9beb-5247f1d08d69" />

<img width="1283" height="751" alt="exa6" src="https://github.com/user-attachments/assets/fe83204a-9b75-4323-9823-50a732687404" />
