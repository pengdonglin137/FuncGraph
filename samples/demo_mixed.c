#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>

static ssize_t read_block(int fd, void *buffer, size_t length)
{
    return read(fd, buffer, length);
}

static ssize_t write_block(int fd, const void *buffer, size_t length)
{
    return write(fd, buffer, length);
}

static int transfer_file(int input, int output, void *buffer, size_t length)
{
    ssize_t bytes = read_block(input, buffer, length);
    return bytes > 0 && write_block(output, buffer, (size_t)bytes) == bytes ? 0 : -1;
}

static ssize_t dispatch_datagram(int fd, const void *buffer, size_t length,
                                 const struct sockaddr_in *peer, socklen_t peer_size)
{
    return sendto(fd, buffer, length, 0, (const struct sockaddr *)peer, peer_size);
}

static ssize_t send_frame(int fd, const void *buffer, size_t length, const struct sockaddr_in *peer)
{
    return dispatch_datagram(fd, buffer, length, peer, sizeof(*peer));
}

static ssize_t receive_frame(int fd, void *buffer, size_t length, int flags)
{
    return recvfrom(fd, buffer, length, flags, NULL, NULL);
}

static int exchange_loopback(int fd, void *buffer, size_t length, const struct sockaddr_in *peer)
{
    const size_t datagram_size = 512;

    if (send_frame(fd, buffer, datagram_size, peer) != (ssize_t)datagram_size)
        return -1;
    if (receive_frame(fd, buffer, length, 0) != (ssize_t)datagram_size)
        return -1;
    return receive_frame(fd, buffer, length, MSG_DONTWAIT) == -1 && errno == EAGAIN ? 0 : -1;
}

static int run_round(int input, int output, int fd, const struct sockaddr_in *peer)
{
    static unsigned char buffer[4096];

    if (transfer_file(input, output, buffer, sizeof(buffer)) < 0)
        return -1;
    return exchange_loopback(fd, buffer, sizeof(buffer), peer);
}

int main(int argc, char **argv)
{
    const char *input_path = argc > 1 ? argv[1] : "/tmp/demo_in.bin";
    const char *output_path = argc > 2 ? argv[2] : "/tmp/demo_out.bin";
    int input = open(input_path, O_RDONLY);
    int output = open(output_path, O_WRONLY);
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    struct sockaddr_in peer = { .sin_family = AF_INET, .sin_addr.s_addr = htonl(INADDR_LOOPBACK) };
    socklen_t peer_size = sizeof(peer);
    int result = 1;

    if (input < 0 || output < 0 || fd < 0 || bind(fd, (struct sockaddr *)&peer, sizeof(peer)) < 0 ||
        getsockname(fd, (struct sockaddr *)&peer, &peer_size) < 0)
        perror("setup");
    else
        result = run_round(input, output, fd, &peer) != 0;
    if (fd >= 0)
        close(fd);
    if (output >= 0)
        close(output);
    if (input >= 0)
        close(input);
    return result;
}
