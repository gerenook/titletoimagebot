""" - """

__version__ = '0.3'

from datetime import datetime
from io import BytesIO
from os import remove

import praw
import requests
from imgurpython import ImgurClient
from imgurpython.helpers.error import (ImgurClientError,
                                       ImgurClientRateLimitError)
from PIL import Image, ImageDraw, ImageFont

import apidata


class Meme:
    """ - """
    font_scale_factor = 20

    def __init__(self, image):
        """Initialize Meme instance

        :param image: the image (meme)
        :type image: PIL Image
        """
        self.meme = image
        self.width, self.height = image.size
        self.font = ImageFont.truetype('segoeui.ttf', self.width // Meme.font_scale_factor)
        self.font_author = ImageFont.truetype('segoeui.ttf', self.width // (Meme.font_scale_factor*2))

    def add_title(self, title):
        """Adds title to new whitespace on meme

        :param title: the title to add
        :type title: str
        """
        line_height = self.font.getsize(title)[1]
        texts = title.split(',')
        if not texts[-1]:
            texts = texts[:-1]
            texts[-1] += ','
        whitespace_height = (line_height * len(texts)) + 10
        new = Image.new('RGB', (self.width, self.height + whitespace_height), '#fff')
        new.paste(self.meme, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            d = ''
            if i < len(texts)-1:
                d = ','
            draw.text((10, i * line_height), text.lstrip() + d, '#000', self.font)
        self.width, self.height = new.size
        self.meme = new

    def add_author(self, author):
        """Adds /u/author to top right of image

        :param author: the author to add
        :type author: str
        """
        text = '/u/' + author
        draw = ImageDraw.Draw(self.meme)
        size = self.font_author.getsize(text)
        pos = (self.width - (size[0] + 10), 0)
        draw.text(pos, text, '#000', self.font_author)

    def upload(self, imgur):
        """Uploads self.meme to imgur

        :param imgur: the imgur api client
        :type imgur: imgurpython.client.ImgurClient
        :returns: imgur url if upload successful, else None
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        self.meme.save(path_png)
        self.meme.save(path_jpg)
        try:
            response = imgur.upload_from_path(path_png)
        except (ImgurClientError, ImgurClientRateLimitError):
            try:
                response = imgur.upload_from_path(path_jpg)
            except (ImgurClientError, ImgurClientRateLimitError):
                return None
        finally:
            remove(path_png)
            remove(path_jpg)
        return response['link']


def main():
    """Main function"""
    imgur = ImgurClient(**apidata.imgur)
    reddit = praw.Reddit(**apidata.reddit)

if __name__ == '__main__':
    main()
