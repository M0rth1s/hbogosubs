![Logo](https://widevine.is-a.fail/e0M2Sb.png)

# hbogosubs

HBO GO Europe subtitle download tool

## Dependencies
* Python 3.6+
* Install package dependencies using `pip install -r requirements.txt`

For Windows, you can download a binary which doesn't require you to install Python or any packages from the [releases page](https://github.com/nyuszika7h/hbogosubs/releases).

## Usage
Run `hbogosubs.py`. On first run after entering a URL you will be prompted to select an operator and enter your username/email and password for that region.

You can specify multiple space-separated URLs as command line arguments. If no URLs are specified, you will be prompted to enter them.

The script attempts to detect whether the entered URLs refer to a movie, a whole series, a season or a single episode.

By default, if a direct SRT subtitle link is available on HBO GO, it will be downloaded. If not available, the script will fall back to downloading and concatenating TTML segments and converting them to SRT. You can force the latter behavior using the `-F` option.

Configuration is saved to the `config` directory next to the script's executable by default, and downloaded subtitles are saved to the current working directory. You can override this using the `-c` and `-o` options, respectively.

If something is wrong, you can use the `--debug` option to attempt to diagnose the issue. Make sure to use this option when reporting issues.

For further help on command line arguments, see `--help`.

## Known issues
* Operator logins with redirect not supported ([#1](https://github.com/nyuszika7h/hbogosubs/issues/1))

## License
[MIT License](LICENSE.txt)
