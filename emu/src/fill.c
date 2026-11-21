/* pm-fill FILE BYTES: reserve BYTES for FILE without writing them, so a test can fill a drive in a moment. The Gen1 firmware has no
 * fallocate command, and writing gigabytes of zeros took minutes of each full_disk run. */
#define _FILE_OFFSET_BITS 64
#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 3) { fprintf(stderr, "usage: pm-fill FILE BYTES\n"); return 2; }
    int fd = open(argv[1], O_WRONLY | O_CREAT, 0644);
    if (fd < 0) { perror(argv[1]); return 1; }
    int e = posix_fallocate(fd, 0, (off_t)strtoll(argv[2], NULL, 10));
    if (e) fprintf(stderr, "%s: %s\n", argv[1], strerror(e));
    close(fd);
    return e != 0;
}
