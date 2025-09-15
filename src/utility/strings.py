import re 
import html
import xml 
import xml.dom.minidom
import json
from gi.repository import GLib

def quote_string(s):
    if "'" in s:
        return "'" + s.replace("'", "'\\''") + "'"
    else:
        return "'" + s + "'"

def markwon_to_pango(markdown_text):
    """
    Converts a subset of Markdown text to Pango markup.

    Supports:
    - Bold: **text** -> <b>text</b>
    - Italic: *text* -> <i>text</i>
    - Monospace: `text` -> <tt>text</tt>
    - Strikethrough: ~text~ -> <span strikethrough="true">text</span>
    - Subscript: _(text) or _digit -> <sub>text</sub> or <sub>digit</sub>
    - Superscript: ^(text) or ^digit -> <sup>text</sup> or <sup>digit</sup>
    - Links: [text](url) -> <a href="url">text</a>
    - Headers: # text -> <span font_weight="bold" font_size="...">text</span> (up to H6)
    - Unordered Lists: -/*/+ item ->   • item (with indentation)
    """
    # Escape potential Pango/XML characters first to avoid issues
    # with user input containing <, >, &
    escaped_text = GLib.markup_escape_text(markdown_text)
    escaped_text = escaped_text.replace("&lt;sub&gt;", "<sub>" )
    escaped_text = escaped_text.replace("&lt;/sub&gt;", "</sub>" )

    escaped_text = escaped_text.replace("&lt;sup&gt;", "<sup>" )
    escaped_text = escaped_text.replace("&lt;/sup&gt;", "</sup>" )
    initial_string = escaped_text # Keep the escaped version as fallback

    processed_text = escaped_text

    # --- Block Formatting ---

    # Convert Unordered Lists
    # Looks for lines starting with optional whitespace, then -, *, or +, then a space.
    # Captures the leading whitespace (indent) and the list item text.
    # Replaces with the original indent, two spaces, a bullet, and the text.
    # Using lambda to reconstruct allows preserving original indent before adding list indent.
    processed_text = re.sub(
        r'^([ \t]*)([-*+])[ \t]+(.*)$',  # Capture: (indent)(marker) (text)
        lambda match: f'{match.group(1)}  • {match.group(3)}', # Replace: indent + "  • " + text
        processed_text,
        flags=re.MULTILINE
    )
    
    # Convert bold text
    processed_text = re.sub(r'\*\*(?!\s)(.*?)(?<!\s)\*\*', r'<b>\1</b>', processed_text)

    # Convert italic text
    processed_text = re.sub(r'(?<!\*)\*(?!\s|\*)(.*?)(?<!\s|\*)\*(?!\*)', r'<i>\1</i>', processed_text)

    # Convert monospace text
    processed_text = re.sub(r'`(.*?)`', r'<tt>\1</tt>', processed_text)

    # Convert strikethrough text
    processed_text = re.sub(r'~(.*?)~', r'<span strikethrough="true">\1</span>', processed_text)

    # Convert exponents and subscripts (handle digits or parenthesized text)
    processed_text = re.sub(r'_(\d+|\([^)]+\))\b', lambda m: f'<sub>{m.group(1).strip("()")}</sub>', processed_text)
    processed_text = re.sub(r'\^(\d+|\([^)]+\))', lambda m: f'<sup>{m.group(1).strip("()")}</sup>', processed_text)
    
    # Convert links
    processed_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', processed_text)


    # Convert headers (needs to be after lists potentially, though headers usually don't have list markers)
    absolute_sizes = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large']
    # Make sure header regex doesn't consume list items if they somehow start with #
    processed_text = re.sub(
        r'^[ \t]*(#+)[ \t]+(.*)$', 
        lambda match: f'<span font_weight="bold" font_size="{absolute_sizes[min(len(absolute_sizes)-1, 6 - len(match.group(1)))]}">{match.group(2).strip()}</span>',
        processed_text,
        flags=re.MULTILINE
    )

    try:
        check_text = processed_text.replace('&', '&')
        xml.dom.minidom.parseString(f"<span>{check_text}</span>")
        return processed_text
    except Exception as e:
        print(f"Pango conversion warning: Generated markup might be invalid. Error: {e}")
        print("Problematic Markup:\n", processed_text)
        return simple_markdown_to_pango(initial_string)

def simple_markdown_to_pango(markdown_text):
    """
    Converts a subset of Markdown text to Pango markup. (Used as a Fallback)

    Supports:
    - Bold: **text** -> <b>text</b>
    - Italic: *text* -> <i>text</i>
    - Links: [text](url) -> <a href="url">text</a>
    - Headers: # text -> <span font_weight="bold" font_size="...">text</span> (up to H6)
    """
    # Escape potential Pango/XML characters first to avoid issues
    # with user input containing <, >, &
    escaped_text = GLib.markup_escape_text(markdown_text)
    initial_string = escaped_text # Keep the escaped version as fallback

    processed_text = escaped_text
 
    # Convert bold text
    processed_text = re.sub(r'\*\*(?!\s)(.*?)(?<!\s)\*\*', r'<b>\1</b>', processed_text)

    # Convert italic text
    processed_text = re.sub(r'(?<!\*)\*(?!\s|\*)(.*?)(?<!\s|\*)\*(?!\*)', r'<i>\1</i>', processed_text)

    # Convert links
    processed_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', processed_text)


    # Convert headers (needs to be after lists potentially, though headers usually don't have list markers)
    absolute_sizes = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large']
    # Make sure header regex doesn't consume list items if they somehow start with #
    processed_text = re.sub(
        r'^[ \t]*(#+)[ \t]+(.*)$', 
        lambda match: f'<span font_weight="bold" font_size="{absolute_sizes[min(len(absolute_sizes)-1, 6 - len(match.group(1)))]}">{match.group(2).strip()}</span>',
        processed_text,
        flags=re.MULTILINE
    )

    try:
        check_text = processed_text.replace('&', '&')
        xml.dom.minidom.parseString(f"<span>{check_text}</span>")
        return processed_text
    except Exception as e:
        print(f"Pango conversion warning (Simple): Generated markup might be invalid. Error: {e}")
        print("Problematic Markup (Simple):\n", processed_text)
        return initial_string

def human_readable_size(size: float, decimal_places:int =2) -> str:
    size = int(size)
    unit = ''
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def extract_json(input_string: str) -> str:
    """Extracts the first valid JSON string from an input string.

    Args:
        input_string: The string to search for a JSON object or array.

    Returns:
        The first valid JSON string found, or an empty JSON object "{}" if none is found.
    """
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(input_string):
        # Find the first opening brace or bracket
        match = None
        first_brace = input_string.find('{', pos)
        first_bracket = input_string.find('[', pos)

        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            pos = first_brace
        elif first_bracket != -1:
            pos = first_bracket
        else:
            # No more JSON objects in the string
            break

        try:
            # Attempt to decode a JSON object from the current position
            obj, end_pos = decoder.raw_decode(input_string[pos:])
            return input_string[pos:pos + end_pos]
        except json.JSONDecodeError:
            # If decoding fails, move to the next character and try again
            pos += 1
            
    return "{}"


def remove_markdown(text: str) -> str:
    """
    Remove markdown from text

    Args:
        text: The text to remove markdown from 

    Returns:
        str: The text without markdown 
    """
    # Remove headers
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    
    # Remove emphasis (bold and italic)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)  # Bold
    text = re.sub(r'__(.*?)__', r'\1', text)          # Bold
    text = re.sub(r'\*(.*?)\*', r'\1', text)        # Italic
    text = re.sub(r'_(.*?)_', r'\1', text)            # Italic
    
    # Remove inline code
    text = re.sub(r'`([^`]*)`', r'\1', text)
    
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)

    # Remove links, keep the link text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)

    # Remove images, keep the alt text
    text = re.sub(r'!\[([^\]]*)\]\([^\)]+\)', r'\1', text)

    # Remove strikethrough
    text = re.sub(r'~~(.*?)~~', r'\1', text)

    # Remove blockquotes
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)

    # Remove unordered list markers
    text = re.sub(r'^\s*[-+*]\s+', '', text, flags=re.MULTILINE)

    # Remove ordered list markers
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # Remove extra newlines
    text = re.sub(r'\n{2,}', '\n', text)

    return text.strip()

def convert_think_codeblocks(text: str) -> str:
    """Convert think codeblocks to markdown

    Args:
        text (str): The text to convert 

    Returns:
        str: The converted text 
    """
    return text.replace("<think>", "```think").replace("</think>", "```")

def remove_thinking_blocks(text):
  """
  Removes <think>...</think> blocks from a given text using regular expressions.

  Args:
    text: The input text string.

  Returns:
    The text string with all <think>...</think> blocks removed.
  """
  pattern = r"<think>.*?</think>"  # Non-greedy match
  cleaned_text = re.sub(pattern, "", text, flags=re.DOTALL) # flags=re.DOTALL allows . to match newline characters
  return cleaned_text

def get_edited_messages(history: list, old_history: list) -> list | None:
    """Get the edited messages from the history

    Args:
        history (list): The history
        prompts (list): The prompts

    Returns:
        list: The edited messages IDs, or None if there are removed messages
    """
    if len(history) != len(old_history):
        return None
    edited_messages = []
    for i in range(len(history)):
        if history[i] != old_history[i]:
            edited_messages.append(i)
    return edited_messages


def add_S_to_sudo(commands_string):
    """
    Adds the -S flag to every sudo command in a string of Linux commands.

    Args:
        commands_string: A string containing Linux commands.

    Returns:
        A string with the -S flag added to all sudo commands.
    """

    def replace_sudo(match):
        command_parts = match.group(0).split()
        if "-S" in command_parts:
            return " ".join(command_parts)  # Already has -S
        
        sudo_index = command_parts.index("sudo")

        if len(command_parts) > sudo_index + 1 and command_parts[sudo_index + 1].startswith("-"):
            #sudo has options, insert -S
            command_parts.insert(sudo_index + 1, "-S")
            return " ".join(command_parts)

        elif len(command_parts) > sudo_index:
            #Insert -S after sudo, no existing option
            command_parts.insert(sudo_index + 1, "-S")
            return " ".join(command_parts)
        else:
            # this case should not happen in normal command string
            return " ".join(command_parts)


    # Use regex to find all "sudo" commands, handling different scenarios
    modified_string = re.sub(r'(^|\s)sudo(\s+[\w\/\.-]+)*', replace_sudo, commands_string)

    return modified_string

def remove_emoji(text):
    emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"  # emoticons
            u"\U0001F300-\U0001F5FF"  # symbols & pictographs
            u"\U0001F680-\U0001F6FF"  # transport & map symbols
            u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "]+", flags=re.UNICODE)
    return emoji_pattern.sub(r'', text)

def replace_codeblock(markdown_text, block_id, new_code):
    """
    Replaces the code block at the given ID (starting from 0) with new_code.
    
    Args:
        markdown_text (str): The full markdown text.
        block_id (int): The index of the code block to replace.
        new_code (str): The new content to put inside the code block.
    
    Returns:
        str: Modified markdown text with the code block replaced.
    """
    pattern = re.compile(r'```.*?\n.*?```', re.DOTALL)
    matches = list(pattern.finditer(markdown_text))
    
    if block_id < 0 or block_id >= len(matches):
        raise IndexError("Code block ID out of range.")
    
    match = matches[block_id]
    # Preserve the language tag from the opening line
    opening_line = match.group(0).split('\n', 1)[0]
    new_block = f"{opening_line}\n{new_code}\n```"

    # Replace the matched code block with the new one
    return markdown_text[:match.start()] + new_block + markdown_text[match.end():]

def clean_bot_response(message_label):
    """Fix the bot response

    Args:
        message_label (): text of the message 
    """
    message_label = message_label.replace('\\\\\\```', "```")
    return message_label 

def rgb_to_hex(r, g, b):
    """
    Convert RGB values from float to hex.

    Args:
        r (float): Red value between 0 and 1.
        g (float): Green value between 0 and 1.
        b (float): Blue value between 0 and 1.

    Returns:
        str: Hex representation of the RGB values.
    """
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def extract_expressions(text, expressions_list):
    expressions = []
    current_expression = None
    current_text = ""

    tokens = text.split()
    i = 0
    while i < len(tokens):
        tokens[i] = tokens[i]
        token = tokens[i].rstrip(".").rstrip("?").rstrip("!").rstrip(",")
        if token.startswith("(") and token.endswith(")"):
            expression = tokens[i][1:-1]
            if expression in [exp.replace("_", "") for exp in expressions_list]:
                if expression not in expressions:
                    expression = expressions_list[[exp.replace("_", "") for exp in expressions_list].index(expression)]
                if current_text.strip():
                    expressions.append({"expression": current_expression, "text": current_text.strip()})
                    current_text = ""
                current_expression = expression
            else:
                current_text += tokens[i] + " "
        else:
            if current_expression is None:
                current_text += tokens[i] + " "
            else:
                current_text += tokens[i] + " "
        i += 1

    if current_expression:
        expressions.append({"expression": current_expression, "text": current_text.strip()})
    else:
        expressions.append({"expression": None, "text": current_text.strip()})

    return expressions
