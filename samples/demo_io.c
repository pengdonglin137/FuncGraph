/* FuncGraph sample workload: one process, one call of each kind.
 *
 *   read   4 KB of a file           (page cache hit: ext4 / filemap_read)
 *   write  4 KB to another, existing file (ext4 buffered write; no O_CREAT, so no inode allocation)
 *   sendto 1 UDP datagram to itself over loopback (udp_sendmsg -> loopback_xmit -> softirq rx)
 *   recvfrom that datagram, then once more with MSG_DONTWAIT on the empty queue
 *          (an error path: -EAGAIN from the UDP receive code back to user space)
 *
 * Captured with set_graph_function = __x64_sys_{read,write,sendto,recvfrom}: open/close and
 * the socket setup are not traced and happen before/after, so the traced calls follow each other.
 *
 * Build: gcc -O2 -static -o demo_io demo_io.c   (static: no dynamic loader open/read in the trace)
 * Run:   ./demo_io [input [output]]  (default /tmp/demo_in.bin, /tmp/demo_out.bin; both must exist)
 */
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

int main(int argc, char **argv)
{
    const char *in = argc > 1 ? argv[1] : "/tmp/demo_in.bin";
    const char *outp = argc > 2 ? argv[2] : "/tmp/demo_out.bin";
    static char buf[4096];

    /* setup first (open, socket, bind are not traced), so the traced calls run back to back */
    int fd = open(in, O_RDONLY);
    int out = open(outp, O_WRONLY);
    int s = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in a = { .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
    socklen_t al = sizeof(a);
    if (fd < 0 || out < 0 || s < 0 || bind(s, (struct sockaddr *)&a, sizeof(a)) < 0 ||
        getsockname(s, (struct sockaddr *)&a, &al) < 0) {
        perror("setup");
        return 1;
    }

    ssize_t n = read(fd, buf, sizeof(buf));
    if (n <= 0 || write(out, buf, (size_t)n) != n)
        return 1;
    if (sendto(s, buf, 512, 0, (struct sockaddr *)&a, sizeof(a)) != 512)
        return 1;
    if (recvfrom(s, buf, sizeof(buf), 0, NULL, NULL) != 512)
        return 1;
    if (recvfrom(s, buf, sizeof(buf), MSG_DONTWAIT, NULL, NULL) >= 0)   /* queue empty: -EAGAIN */
        return 1;

    close(s);
    close(out);
    close(fd);
    return 0;   /* no printf: it would add a write() to the trace */
}
