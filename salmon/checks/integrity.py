import os
import subprocess
from hashlib import md5

import click
from mutagen import flac

from salmon import config
from salmon.common.figles import process_files

# Constants for file processing
CHUNK_SIZE = 512 * 1024


def handle_integrity_check(path, md5_only=False):
    """
    Check integrity of FLAC files and prompt user to sanitize if issues are found.

    Args:
        path: Directory or file to check
        md5_only: Whether to only update MD5 signatures without re-encoding

    Returns:
        dict: Results of the integrity check and sanitization if performed
    """
    # Check integrity and get list of files needing sanitization
    check_results = check_integrity(path)

    if check_results["failed"] == 0:
        return check_results  # All files passed, nothing to fix

    # Display files that need sanitization with their error messages
    if len(check_results["needs_sanitization"]) > 0:
        click.secho("\nFiles that need sanitization:", fg="yellow", bold=True)
        for i, file_path in enumerate(check_results["needs_sanitization"], 1):
            # Get just the filename for cleaner display
            click.secho(f"\n{i}. {_get_shortened_path(file_path, max_dirs=4)}", fg="yellow")

            for error in check_results["error_messages"][file_path]:
                # Format error messages cleanly
                click.secho(f"   - {error}", fg="red")

        # Prompt user for confirmation to sanitize
        if click.confirm("\nDo you want to sanitize these files?", default=False):
            sanitize_mode = "MD5 signatures only" if md5_only else "full sanitization"
            click.secho(f"\nPerforming {sanitize_mode}...", fg="blue", bold=True)

            sanitize_results = sanitize_integrity(check_results["needs_sanitization"], md5_only)
            check_results["sanitized"] = sanitize_results["sanitized"]
            check_results["sanitize_failed"] = sanitize_results["failed"]

            return check_results
        else:
            click.secho("Sanitization cancelled.", fg="yellow")
            raise click.Abort()

    return check_results


def check_integrity(path):
    """
    Check the integrity of a directory or file.

    Args:
        path: Path to check

    Returns:
        dict: Results containing stats and files needing sanitization with error messages
    """
    click.secho(f"Checking integrity of {path}", fg="blue", bold=True)

    results = {
        "checked": 0,
        "ok": 0,
        "failed": 0,
        "needs_sanitization": [],  # List to store files that need sanitization
        "error_messages": {},  # Dictionary to store error messages by file path
    }

    # Collect audio files to check
    audio_files = []
    if os.path.isfile(path):
        extension = os.path.splitext(path)[1].lower()
        if extension in [".flac", ".mp3"]:
            audio_files.append(path)
    else:
        for root, _, files in os.walk(path):
            for file in files:
                file_path = os.path.join(root, file)
                extension = os.path.splitext(file_path)[1].lower()
                if extension in [".flac", ".mp3"]:
                    audio_files.append(file_path)

    if not audio_files:
        click.secho(f"No audio files found in {path}", fg="yellow")
        return results

    # Process files concurrently
    process_results = process_files(audio_files, check_file, "Checking audio files")

    # Compile results
    results["checked"] = len(process_results)

    for file_result in process_results:
        if file_result["success"]:
            results["ok"] += 1
        else:
            results["failed"] += 1
            if file_result["needs_sanitization"]:
                results["needs_sanitization"].append(file_result["path"])
                results["error_messages"][file_result["path"]] = file_result["error_messages"]

    # Report results
    click.secho(f"Integrity check results for {path}:", fg="blue", bold=True)
    click.secho(f"  {results['checked']} files checked", fg="blue")
    click.secho(f"  {results['ok']} files OK", fg="green")

    if results["failed"] > 0:
        click.secho(f"  {results['failed']} files failed", fg="red", bold=True)
        if len(results["needs_sanitization"]) > 0:
            click.secho(f"  {len(results['needs_sanitization'])} FLAC files can be sanitized", fg="yellow")
    else:
        click.secho("  All files passed integrity check!", fg="green", bold=True)

    return results


def check_file(file_path, idx=None):
    """
    Check integrity of a single file and immediately display results.

    Args:
        file_path: Path to the file
        idx: Index parameter from process_files (not used but must be accepted)

    Returns:
        dict: Results for this file
    """

    def _display_errors(path, errors):
        click.secho(" FAILED", fg="red")
        click.secho(f"  Issues with {_get_shortened_path(path, max_dirs=4)}:", fg="yellow")
        for error in errors:
            click.secho(f"    - {error} \n", fg="red")
        click.echo()  # Add an empty line for better readability

    result = {"path": file_path, "success": False, "needs_sanitization": False, "error_messages": []}
    extension = os.path.splitext(file_path)[1].lower()

    if extension == ".flac":
        integrity_ok, errors = _check_flac_integrity(file_path)
        result["success"] = integrity_ok
        result["error_messages"] = errors
        result["needs_sanitization"] = not integrity_ok
        if not integrity_ok:
            _display_errors(file_path, errors)

    elif extension == ".mp3":
        integrity_ok, errors = _check_mp3_integrity(file_path)
        result["success"] = integrity_ok
        result["error_messages"] = errors
        result["needs_sanitization"] = not integrity_ok

    else:
        click.secho(" SKIPPED (unsupported format)", fg="yellow")

    return result


def _check_flac_integrity(path):
    """
    Check the integrity of a FLAC file using flac -wt and verify MD5 signature.

    Args:
        path: Path to the FLAC file

    Returns:
        tuple: (integrity_ok, error_messages)
            integrity_ok (bool): True if file integrity is good, False otherwise
            error_messages (list): List of error messages if any issues were found
    """
    integrity_ok = True
    error_messages = []

    # Run the flac -wt integrity check
    try:
        result = subprocess.run(["flac", "-wt", path], capture_output=True, text=True)

        if result.returncode != 0:
            # Extract only the important error information
            error_message = _extract_important_error_info(result.stderr)
            error_messages.append(f"FLAC integrity check failed: \n {error_message}")
            integrity_ok = False

        # Check for warnings in stderr - some issues might not cause a non-zero exit code
        if result.stderr and ("WARNING" in result.stderr):
            # Extract only the important warning information
            warning_message = _extract_important_error_info(result.stderr)
            # Check for similarity with existing messages
            if not _is_message_similar_to_existing(warning_message, error_messages):
                error_messages.append(f"FLAC integrity check warnings: \n {warning_message}")
            integrity_ok = False

    except Exception as e:
        error_messages.append(f"Error running FLAC integrity check: {str(e)}")
        integrity_ok = False

    # Perform the MD5 verification as part of the same integrity check
    try:
        flac_file = flac.FLAC(path)
        stored_md5 = flac_file.info.md5_signature

        if stored_md5 == 0:
            error_messages.append("No MD5 signature present")
            integrity_ok = False
        else:
            calculated_md5 = int(get_md5(path), 16)

            if stored_md5 != calculated_md5:
                error_messages.append(f"MD5 mismatch - Stored: {hex(stored_md5)}, Calculated: {hex(calculated_md5)}")
                integrity_ok = False
    except Exception as e:
        error_messages.append(f"Error checking MD5: {str(e)}")
        integrity_ok = False

    return integrity_ok, error_messages


def _check_mp3_integrity(path):
    """Check the integrity of an MP3 file."""
    integrity_ok = True
    error_messages = []
    try:
        result = subprocess.run(["mp3val", "-si", path], capture_output=True, text=True)
        if result.returncode != 0:
            click.secho(f"MP3 integrity check failed for {path}: {result.stderr}", fg="red", bold=True)
            error_messages.append(f"  {result.stderr}")
            integrity_ok = False

        if any(level in result.stdout for level in ("WARNING", "ERROR", "INFO")):
            click.secho(f"MP3 integrity check failed for {path}:", fg="red", bold=True)
            for line in result.stdout.splitlines():
                click.secho(f"  {line}", fg="yellow" if "WARNING" in line or "INFO" in line else "red")
                error_messages.append(f"  {line}")
            integrity_ok = False

    except Exception as e:
        click.secho(f"Error running MP3 integrity check for {path}: {e}", fg="red", bold=True)
        integrity_ok = False

    return integrity_ok, error_messages


def _sanitize_flac(path, md5_only=False):
    """
    Sanitize a FLAC file.

    Args:
        path: Path to the FLAC file
        md5_only: If True, only updates the MD5 value without modifying the file structure
                  If False, performs full sanitization including re-encoding

    Returns:
        bool: True if successful, False otherwise
    """
    extension = os.path.splitext(path)[1].lower()
    try:
        if md5_only and extension == ".flac":
            # Only update the MD5 value without modifying the file
            success, updated = set_md5(path)
            if success and updated:
                click.secho(f"Updated MD5 signature for {path}", fg="green")
            return success
        else:
            # Perform full sanitization
            os.rename(path, path + ".corrupted")
            result = subprocess.run(
                ["flac", f"-{config.FLAC_COMPRESSION_LEVEL}", path + ".corrupted", "-o", path],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise Exception(f"FLAC encoding failed:\n{result.stdout}\n{result.stderr}")
            os.remove(path + ".corrupted")
            result = subprocess.run(
                ["metaflac", "--dont-use-padding", "--remove", "--block-type=PADDING,PICTURE", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if result.returncode != 0:
                raise Exception("Failed to remove FLAC metadata blocks")
            result = subprocess.run(
                ["metaflac", "--add-padding=8192", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            if result.returncode != 0:
                raise Exception("Failed to add FLAC padding")
            return True
    except Exception as e:
        click.secho(f"Failed to sanitize {path}, {e}", fg="red", bold=True)
        return False


def _sanitize_mp3(path):
    # Load the MP3 file with mutagen
    try:
        from mutagen.mp3 import MP3, HeaderNotFoundError


        mp3_file = MP3(path)
        # Simply save it back to rewrite the file with proper structure
        mp3_file.save()
        return True
    except HeaderNotFoundError:
        # This is the "can't sync to MPEG frame" error
        error = "Error: Can\'t sync to MPEG frame. The MP3 file appears to be corrupted."
        click.secho(
            f'Failed to sanitize {path} - {error} ', fg="red", bold=True)
    except Exception as e:
        click.secho(f"Failed to sanitize {path}, {e}", fg="red", bold=True)
        return False


def sanitize_integrity(files_to_sanitize, md5_only=False):
    """
    Sanitize FLAC files that failed integrity check.

    Args:
        files_to_sanitize: List of file paths to sanitize
        md5_only: Whether to only update MD5 signatures without re-encoding

    Returns:
        dict: Results of sanitization operation
    """
    results = {"total": len(files_to_sanitize), "sanitized": 0, "failed": 0}

    if not files_to_sanitize:
        click.secho("No files to sanitize.", fg="green")
        return results

    click.secho(f"Sanitizing {len(files_to_sanitize)} files...", fg="blue", bold=True)

    def sanitize_file(file_path, md5_only=False):
        result = {"path": file_path, "success": False}
        click.secho(f"Sanitizing {file_path}", fg="blue")
        extension = os.path.splitext(file_path)[1].lower()
        if extension == ".mp3":
            result["success"] = _sanitize_mp3(file_path)
        elif extension == ".flac":
            result["success"] = _sanitize_flac(file_path, md5_only)
        else:
            click.secho(f"File type {extension} is not supported", fg="red")
            result["success"] = False
        return result
    # Process files concurrently
    process_results = process_files(
        files_to_sanitize, lambda path, idx: sanitize_file(path, md5_only), "Sanitizing files"
    )

    # Compile results
    for file_result in process_results:
        if file_result["success"]:
            results["sanitized"] += 1
        else:
            results["failed"] += 1

    # Report results
    click.secho("Sanitization completed:", fg="blue", bold=True)
    click.secho(f"  {results['sanitized']} files sanitized successfully", fg="green")

    if results["failed"] > 0:
        click.secho(f"  {results['failed']} files failed to sanitize", fg="red", bold=True)

    return results


def format_integrity_check(result):
    """Format the integrity check result for display"""
    integrities, integrities_out = result
    if integrities:
        return click.style("Passed integrity check", fg="green")
    else:
        output = click.style("Failed integrity check", fg="red", bold=True)
        if integrities_out:
            output += f"\nDetails:\n{integrities_out}"
        return output


def _is_message_similar_to_existing(new_message, existing_messages, similarity_threshold=0.7):
    """
    Check if a message is similar to any existing messages using fuzzy matching.

    Args:
        new_message: The new message to check
        existing_messages: List of existing messages
        similarity_threshold: Threshold for considering messages similar (0.0 to 1.0)

    Returns:
        bool: True if the new message is similar to any existing message
    """

    def _similarity_score(str1, str2):
        """
        Calculate similarity score between two strings using sequence matcher.

        Returns:
            float: Similarity score between 0.0 and 1.0
        """
        from difflib import SequenceMatcher

        return SequenceMatcher(None, str1, str2).ratio()

    # Calculate normalized form of the new message (lowercase, no extra spaces)
    normalized_new = " ".join(new_message.lower().split())

    for existing in existing_messages:
        # Calculate normalized form of existing message
        normalized_existing = " ".join(existing.lower().split())

        # Calculate similarity score
        score = _similarity_score(normalized_new, normalized_existing)

        # If similarity is above threshold, consider them similar
        if score >= similarity_threshold:
            return True

    # No similar messages found
    return False


def _get_shortened_path(file_path, max_dirs=4):
    """
    Shortens a file path to include at most a specified number of directories.

    Args:
        file_path: The full file path
        max_dirs: Maximum number of directory components to include

    Returns:
        str: Shortened file path with at most the specified number of directories
    """
    # Split the path into components
    parts = os.path.normpath(file_path).split(os.sep)

    # If there are fewer parts than max_dirs + 1 (for filename), return the full path
    if len(parts) <= max_dirs + 1:
        return file_path

    # Get the last max_dirs directories plus the filename
    shortened_parts = parts[-max_dirs - 1 :]

    # If the path was shortened, add an ellipsis at the beginning
    if len(shortened_parts) < len(parts):
        shortened_parts[0] = f"...{os.sep}{shortened_parts[0]}"

    # Join the parts back together
    return os.path.join(*shortened_parts)


def get_md5(flac_path: str) -> str:
    """Calculate MD5 hash of FLAC audio data using flac command line tool."""
    md_five = md5()
    with subprocess.Popen(
        ["flac", "-ds", "--stdout", "--force-raw-format", "--endian=little", "--sign=signed", flac_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ) as decoding:
        for chunk in iter(lambda: decoding.stdout.read(CHUNK_SIZE), b""):
            md_five.update(chunk)

    return md_five.hexdigest()


def get_flac_md5(path: str):
    """Get the MD5 signature from a FLAC file."""
    try:
        flac_file = flac.FLAC(path)
        return flac_file.info.md5_signature
    except Exception:
        return 0


def set_md5(path: str):
    """Set the correct MD5 signature in a FLAC file."""
    try:
        flac_file = flac.FLAC(path)
        md5_hex = get_md5(path)
        calculated_md5 = int(md5_hex, 16)

        # Only update if the MD5 is missing or incorrect
        if flac_file.info.md5_signature != calculated_md5:
            flac_file.info.md5_signature = calculated_md5
            flac_file.save()
            return True, True  # Success and updated
        return True, False  # Success but no update needed
    except Exception as e:
        click.secho(f"Failed to set MD5 for {path}: {e}", fg="red", bold=True)
        return False, False  # Failed


def _extract_important_error_info(error_output):
    """
    Extract only important error information from FLAC verification output.

    Args:
        error_output: Full error output from flac command

    Returns:
        str: Concise error message with only relevant information
    """
    important_lines = []

    # click.secho(f"Error message: {error_output}", fg="cyan", bold=True)
    # Split the error output into lines
    lines = error_output.splitlines()
    # click.secho(f"Lines: {lines}", fg="blue", bold=True)

    # Filter lines containing specific error indicators
    for line in lines:
        # Look for error codes and important messages
        if any(
            pattern in line
            for pattern in [
                "error code",
                "ERROR",
                "FLAC__STREAM_DECODER_ERROR",
                "state =",
                "MD5 mismatch",
                "failed",
                "WARNING",
            ]
        ):
            # Clean up line by removing any file prefix like "filename.flac: "
            cleaned_line = line
            if ": " in cleaned_line and ".flac: " in cleaned_line:
                cleaned_line = cleaned_line.split(".flac: ", 1)[1]

            important_lines.append(cleaned_line.strip())

    # Return filtered output or a default message if no specific lines found
    if important_lines:
        return "\n".join("- " + line for line in important_lines)
    else:
        # If we can't identify specific lines but know there was an error
        return "Unspecified FLAC integrity error"