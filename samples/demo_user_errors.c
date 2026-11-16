#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int application_result(void)
{
    return -3;
}

int main(int argc, char **argv)
{
    char buffer[1];
    void *allocation = NULL;
    int descriptor = open("/funcgraph-user-api-failures/no-such-file", O_RDONLY);
    FILE *stream = fopen("/funcgraph-user-api-failures/no-such-file", "r");
    ssize_t count = read(-1, buffer, sizeof(buffer));
    int aligned = posix_memalign(&allocation, 3, 16);
    int compared = strcmp(argc > 1 ? argv[1] : "a", "z");
    int application = application_result();
    return descriptor == -1 && stream == NULL && count == -1 && aligned != 0 && compared < 0 && application == -3 ? 0 : 1;
}
