#!/usr/bin/env python3

from aws_cdk import core

from m_lflow.m_lflow_stack import MLflowStack


app = core.App()
MLflowStack(app, "m-lflow", env={'region': 'us-east-1'})

app.synth()
