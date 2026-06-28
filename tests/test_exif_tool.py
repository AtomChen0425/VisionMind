import exiftool
import rawpy
import PIL
from PIL import Image, ImageOps,ExifTags
from collections import defaultdict
from typing import Any
image_path=r"F:\相片\20251020湖边\枫叶和塔\DSC_8903.NEF"
EXIFTOOL_PATH = r"D:\Coding\Git_repositories\PhotoManager\data\tools\exiftool\exiftool-13.59_64\exiftool(-k).exe" 
# with exiftool.ExifToolHelper(executable=EXIFTOOL_PATH,encoding="utf-8") as et:
#     metadata = et.set_tags(image_path,)
# with rawpy.imread(image_path) as raw:
#         rgb = raw.postprocess(
#             use_camera_wb=True,
#         )
# PIL.Image.fromarray(rgb).save('image.jpg', quality=90, optimize=True)
# print(metadata)
with Image.open(image_path) as image:
    img_exif = image.getexif()
    # if not img_exif:
    #     return get_img_xmp(image)

    result_dict: dict[str, Any] = defaultdict(str)
    for key, val in img_exif.items():
        tag_name = ExifTags.TAGS.get(key)
        if tag_name:
            result_dict[tag_name] = val
print(result_dict)
    # xmp_data = get_img_xmp(image)
    # for key, value in xmp_data.items():
    #     result_dict.setdefault(key, value)

