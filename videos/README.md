# Test videos

Use `movie.Mjpeg` from the supplied Python teaching example. A copy was placed
here for development. The current `.gitignore` explicitly permits this sample;
other media files remain ignored. Ensure the sample is present in the CORE VM.

This is the book's length-prefixed format: five ASCII decimal bytes followed by
that many JPEG bytes, repeated for each frame. It is not an arbitrary `.mjpeg`
container. Only the streaming server needs the file.

Start the server with `--video videos/movie.Mjpeg`, and clients with `--watch`.
The default is 20 FPS with continuous looping; frame numbers keep increasing
across loops. Source adaptation is documented in the project README.
