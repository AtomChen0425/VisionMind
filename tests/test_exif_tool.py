import exiftool
image_path=r"F:\相片\20251020湖边\枫叶和塔\DSC_7439.NEF"
EXIFTOOL_PATH = r"D:\Coding\Git_repositories\PhotoManager\data\tools\exiftool\exiftool-13.59_64\exiftool(-k).exe" 
with exiftool.ExifToolHelper(executable=EXIFTOOL_PATH,encoding="utf-8") as et:
    metadata = et.set_tags(image_path,)

print(metadata)