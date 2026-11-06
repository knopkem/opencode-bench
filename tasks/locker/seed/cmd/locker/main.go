package main

import (
	"os"

	"locker/internal/cli"
)

func main() {
	os.Exit(cli.Run(os.Args[1:]))
}
