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


def main():
    """Main function"""
    imgur = ImgurClient(**apidata.imgur)
    reddit = praw.Reddit(**apidata.reddit)

if __name__ == '__main__':
    main()
