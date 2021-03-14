from aws_cdk import (
    aws_ec2 as ec2,
    aws_s3 as s3,
    aws_ecs as ecs,
    aws_rds as rds,
    aws_iam as iam,
    aws_secretsmanager as sm,
    aws_ecs_patterns as ecs_patterns,
    core
)


class MLflowStack(core.Stack):

    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)
        ##
        ##Parametros gerais utilizados para provisioamento de infra
        ##
        project_name_param = core.CfnParameter(scope=self, id='mlflowStack', type='String', default='mlflowStack')
        db_name = 'mlflowdb'
        port = 3306
        username = 'master'
        bucket_name = 'mlflowbucket-track-stack'
        container_repo_name = 'mlflow-containers'
        cluster_name = 'mlflow'
        service_name = 'mlflow'

        #Associação das policys gerenciadas a role que sera atribuida a task ECS.        
        role = iam.Role(scope=self, id='TASKROLE', assumed_by=iam.ServicePrincipal(service='ecs-tasks.amazonaws.com'))
        
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name('AmazonS3FullAccess'))
        
        role.add_managed_policy(iam.ManagedPolicy.from_aws_managed_policy_name('AmazonECS_FullAccess'))

        #Secrets Manager responsavel pelo armazenamento do password do nosso RDS MySQL
        db_password_secret = sm.Secret(
            scope=self,
            id='dbsecret',
            secret_name='dbPassword',
            generate_secret_string=sm.SecretStringGenerator(password_length=20, exclude_punctuation=True)
        )

         #Criação do Bucket S3
        artifact_bucket = s3.Bucket(
            scope=self,
            id='mlflowstacktrack',
            bucket_name=bucket_name,
            public_read_access=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=core.RemovalPolicy.DESTROY
        )

      #Obtenção de VPC para atribuição ao RDS
      dev_vpc = ec2.Vpc.from_vpc_attributes(
            self, '<VPC_NAME>',
            vpc_id = "<VPC_ID>",
            availability_zones = core.Fn.get_azs(),
            private_subnet_ids = ["PRIVATE_SUBNET_ID_1","PRIVATE_SUBNET_ID_2","PRIVATE_SUBNET_ID_3"]
       )

        # Adicionamos aqui para efeito de testes 0.0.0.0/0
        sg_rds = ec2.SecurityGroup(scope=self, id='SGRDS', security_group_name='sg_rds', vpc=dev_vpc)
        
        sg_rds.add_ingress_rule(peer=ec2.Peer.ipv4('0.0.0.0/0'), connection=ec2.Port.tcp(port))

        # Criação da instancia RDS
        database = rds.DatabaseInstance(
            scope=self,
            id='MYSQL',
            database_name=db_name,
            port=port,
            credentials=rds.Credentials.from_username(username=username, password=db_password_secret.secret_value),
            engine=rds.DatabaseInstanceEngine.mysql(version=rds.MysqlEngineVersion.VER_8_0_19),
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.BURSTABLE2, ec2.InstanceSize.SMALL),            
            security_groups=[sg_rds],
            vpc=dev_vpc,            
            # multi_az=True,
            removal_policy=core.RemovalPolicy.DESTROY,
            deletion_protection=False
        )

        #Criação do Cluster ECS
        cluster = ecs.Cluster(scope=self, id='CLUSTER', cluster_name=cluster_name)
        #Task Definition para Fargate
        task_definition = ecs.FargateTaskDefinition(
            scope=self,
            id='MLflow',
            task_role=role,

        )
        #Criando nosso container com base no Dockerfile do MLflow
        container = task_definition.add_container(
            id='Container',
            image=ecs.ContainerImage.from_asset(
                directory='../MLflow/container',
                repository_name=container_repo_name
            ),
             #Atribuição Variaves ambiente
            environment={
                'BUCKET': f's3://{artifact_bucket.bucket_name}',
                'HOST': database.db_instance_endpoint_address,
                'PORT': str(port),
                'DATABASE': db_name,
                'USERNAME': username
            },
            #Secrets contendo o password do RDS MySQL
            secrets={
                'PASSWORD': ecs.Secret.from_secrets_manager(db_password_secret)
            }
        )
        #Port Mapping para exposição do Container MLflow
        port_mapping = ecs.PortMapping(container_port=5000, host_port=5000, protocol=ecs.Protocol.TCP)
        container.add_port_mappings(port_mapping)

        fargate_service = ecs_patterns.NetworkLoadBalancedFargateService(
            scope=self,
            id='MLFLOW',
            service_name=service_name,
            cluster=cluster,
            task_definition=task_definition
        )

        #Security group para ingress
        fargate_service.service.connections.security_groups[0].add_ingress_rule(
            peer=ec2.Peer.ipv4('0.0.0.0/0'),
            connection=ec2.Port.tcp(5000),
            description='Allow inbound for mlflow'
        )

        #Auto Scaling Policy para nosso balanceador
        scaling = fargate_service.service.auto_scale_task_count(max_capacity=2)
        scaling.scale_on_cpu_utilization(
            id='AUTOSCALING',
            target_utilization_percent=70,
            scale_in_cooldown=core.Duration.seconds(60),
            scale_out_cooldown=core.Duration.seconds(60)
        )
     
        core.CfnOutput(scope=self, id='LoadBalancerDNS', value=fargate_service.load_balancer.load_balancer_dns_name)

    