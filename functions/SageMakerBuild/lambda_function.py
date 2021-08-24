import json
import boto3
import requests
import botocore
import time
import base64

## Request Status ##
global ReqStatus


def CFTFailedResponse(event, status, message):
    print("Inside CFTFailedResponse")
    responseBody = {
        'Status': status,
        'Reason': message,
        'PhysicalResourceId': event['ServiceToken'],
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId']
    }
	
    headers={
        'content-type':'',
        'content-length':str(len(json.dumps(responseBody)))	 
    }	
    print('Response = ' + json.dumps(responseBody))
    try:	
        req=requests.put(event['ResponseURL'], data=json.dumps(responseBody),headers=headers)
        print("delete_respond_cloudformation res "+str(req))		
    except Exception as e:
        print("Failed to send cf response {}".format(e))
        
def CFTSuccessResponse(event, status, data=None):
    responseBody = {
        'Status': status,
        'Reason': 'See the details in CloudWatch Log Stream',
        'PhysicalResourceId': event['ServiceToken'],
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data
    }
    headers={
        'content-type':'',
        'content-length':str(len(json.dumps(responseBody)))	 
    }	
    print('Response = ' + json.dumps(responseBody))
    #print(event)
    try:	
        req=requests.put(event['ResponseURL'], data=json.dumps(responseBody),headers=headers)
    except Exception as e:
        print("Failed to send cf response {}".format(e))


def lambda_handler(event, context):
    ReqStatus = "SUCCESS"
    print("Event:")
    print(event)
    client = boto3.client('sagemaker')
    ec2client = boto3.client('ec2')
    data = {}

    if event['RequestType'] == 'Create':
        try:
            ## Value Intialization from CFT ##
            project_name = event['ResourceProperties']['ProjectName']
            kmsKeyId = event['ResourceProperties']['KmsKeyId']
            Tags = event['ResourceProperties']['Tags']
            LC_Status = event['ResourceProperties']['LifecycleConfig']
            env_name = event['ResourceProperties']['ENVName']
            subnet_name1 = event['ResourceProperties']['Subnet1']

            input_dict = {}
            input_dict['NotebookInstanceName'] = event['ResourceProperties']['NotebookInstanceName']
            input_dict['InstanceType'] = event['ResourceProperties']['NotebookInstanceType']
            input_dict['Tags'] = event['ResourceProperties']['Tags']
            input_dict['DirectInternetAccess'] = event['ResourceProperties']['DirectInternetAccess']
            input_dict['RootAccess'] = event['ResourceProperties']['RootAccess']
            input_dict['VolumeSizeInGB'] = int(event['ResourceProperties']['VolumeSizeInGB'])
            input_dict['RoleArn'] = event['ResourceProperties']['RoleArn']

        except Exception as e:
            print(e)
            ReqStatus = "FAILED"
            message = "Parameter Error: "+str(e)
            CFTFailedResponse(event, "FAILED", message)
        if ReqStatus == "FAILED":
            return None;
        print("Validating Environment name: "+env_name)
        print("Subnet Id Fetching.....")
        try:
            ## Sagemaker Subnet ##
            subnetName = env_name+"-ResourceSubnet"
            print(subnetName)
            response = ec2client.describe_subnets(
                Filters=[
                    {
                        'Name': 'tag:Name',
                        'Values': [
                            subnet_name1
                        ]
                    },
                ]
            )
            #print(response)
            subnetId = response['Subnets'][0]['SubnetId']
            input_dict['SubnetId'] = subnetId
        except Exception as e:
            print(e)
            ReqStatus = "FAILED"
            message = " Project Name is invalid - Subnet Error: "+str(e)
            CFTFailedResponse(event, "FAILED", message)
        if ReqStatus == "FAILED":
            return None;
        ## Sagemaker Security group ##
        print("Security GroupId Fetching.....")
        try:
            sgName = env_name+"-ResourceSG"
            response = ec2client.describe_security_groups(
                Filters=[
                    {
                        'Name': 'tag:Name',
                        'Values': [
                            sgName
                        ]
                    },
                ]
            )
            sgId = response['SecurityGroups'][0]['GroupId']
            input_dict['SecurityGroupIds'] = [sgId]
        except Exception as e:
            print(e)
            ReqStatus = "FAILED"
            message = "Security Group ID Error: "+str(e)
            CFTFailedResponse(event, "FAILED", message)
        if ReqStatus == "FAILED":
            return None;    
        
        try:
            if kmsKeyId:
                input_dict['KmsKeyId'] = kmsKeyId
            if LC_Status == 'YES' :
                S3BucketName = event['ResourceProperties']['CodeBucketName']
                OldLCName = env_name + "-EFS-LC"
                NewLCName = project_name + "-EFS-S3-LC"
                
                ## Describing existing Lifecycle Configuration ##
                response = client.describe_notebook_instance_lifecycle_config(
                    NotebookInstanceLifecycleConfigName=OldLCName
                )
                script = str(base64.b64decode(response['OnCreate'][0]['Content'])).split('\\n')
                
                ## Creation of EFS and S3 LC ##
                new_script = "#!/bin/bash \n"
                for line in range(1,len(script)-1):
                    new_script = new_script + script[line] + "\n"
                
                 # Update S3 bucket name in script #
                new_script = new_script + "sudo chown ec2-user:ec2-user /home/ec2-user/SageMaker/efs --recursive \n" + "# Copy Content \n" + "aws s3 cp s3://" + S3BucketName + " /home/ec2-user/SageMaker/ --recursive \n" + "aws configure set sts_regional_endpoints regional \n" + "yes | cp -rf ~/.aws/config /home/ec2-user/.aws/config"
                content = base64.b64encode(new_script.encode()).decode()
                
                # STS endpoint update in start launch configuration #
                start_script = "#!/bin/bash \n" + "aws configure set sts_regional_endpoints regional \n" + "yes | cp -rf ~/.aws/config /home/ec2-user/.aws/config"
                start_content = base64.b64encode(start_script.encode()).decode()
                
                response = client.create_notebook_instance_lifecycle_config(
                    NotebookInstanceLifecycleConfigName=NewLCName,
                    OnCreate=[
                        {
                            'Content': content
                        }
                    ],
                    OnStart=[
                        {
                            'Content': start_content
                        },
                    ]
                )
                input_dict['LifecycleConfigName'] = NewLCName
            else:
                input_dict['LifecycleConfigName'] = env_name + "-EFS-LC"
                
            print(input_dict)
            instance = client.create_notebook_instance(**input_dict)
            print('Sagemager CLI response')
            print(str(instance))
            responseData = {'NotebookInstanceArn': instance['NotebookInstanceArn']}
            
            NotebookStatus = 'Pending'
            response = client.describe_notebook_instance(
                NotebookInstanceName=event['ResourceProperties']['NotebookInstanceName']
            )
            NotebookStatus = response['NotebookInstanceStatus']
            print("NotebookStatus:"+NotebookStatus)
            
            ## Notebook Failure ##
            if NotebookStatus == 'Failed':
                message = NotebookStatus+": "+response['FailureReason']+" :Notebook is not coming InService"
                CFTFailedResponse(event, "FAILED", message)
            else:
                time.sleep(400)
                response = client.describe_notebook_instance(
                    NotebookInstanceName=event['ResourceProperties']['NotebookInstanceName']
                )
                NotebookStatus = response['NotebookInstanceStatus']
                print("NotebookStatus:"+NotebookStatus)
                
                ## Notebook Success ##
                if NotebookStatus == 'InService':
                    data['Message'] = "SageMaker Notebook name - "+event['ResourceProperties']['NotebookInstanceName']+" created succesfully"
                    CFTSuccessResponse(event, "SUCCESS", data)
                else:
                    message = NotebookStatus+": "+response['FailureReason']+" :Notebook is not coming InService"
                    CFTFailedResponse(event, "FAILED", message)
        except Exception as e:
            print(e)
            ReqStatus = "FAILED"
            CFTFailedResponse(event, "FAILED", str(e))
    if event['RequestType'] == 'Delete':
        NotebookStatus = None
        project_name = event['ResourceProperties']['ProjectName']
        NotebookName = event['ResourceProperties']['NotebookInstanceName']
        LC_Status = event['ResourceProperties']['LifecycleConfig']
        
        ## SageMaker lifecycle config deletion ##
        if LC_Status == 'YES' :
            LCName = project_name + "-EFS-S3-LC"
            try:
                response = client.delete_notebook_instance_lifecycle_config(
                    NotebookInstanceLifecycleConfigName=LCName
                )
                print(LCName+" SageMaker lifecycle config deleted")
            except Exception as e:
                print("SageMaker lifecycle config deletion failed {}".format(e))
        
        try:
            response = client.describe_notebook_instance(
                NotebookInstanceName=NotebookName
            )
            NotebookStatus = response['NotebookInstanceStatus']
            print("Notebook Status - "+NotebookStatus)
        except Exception as e:
            print(e)
            NotebookStatus = "Invalid"
            #CFTFailedResponse(event, "FAILED", str(e))
        while NotebookStatus == 'Pending':
            time.sleep(30)
            response = client.describe_notebook_instance(
                NotebookInstanceName=NotebookName
            )
            NotebookStatus = response['NotebookInstanceStatus']
            print("NotebookStatus:"+NotebookStatus)
        if NotebookStatus != 'Failed' and NotebookStatus != 'Invalid' :
            print("Delete request for Notebookk name: "+NotebookName)
            print("Stoping the Notebook.....")
            if NotebookStatus != 'Stopped':
                try:
                    response = client.stop_notebook_instance(
                        NotebookInstanceName=NotebookName
                    )
                    NotebookStatus = 'Stopping'
                    print("Notebook Status - "+NotebookStatus)
                    while NotebookStatus == 'Stopping':
                        time.sleep(30)
                        response = client.describe_notebook_instance(
                            NotebookInstanceName=NotebookName
                        )
                        NotebookStatus = response['NotebookInstanceStatus']
                    print("NotebookStatus:"+NotebookStatus)
                except Exception as e:
                    print(e)
                    NotebookStatus = "Invalid"
                    CFTFailedResponse(event, "FAILED", str(e))
                
            else:
                NotebookStatus = 'Stopped'
                print("NotebookStatus:"+NotebookStatus)
        
        if NotebookStatus != 'Invalid':
            print("Deleting The Notebook......")
            time.sleep(5)
            try:
                response = client.delete_notebook_instance(
                    NotebookInstanceName=NotebookName
                )
                print("Notebook Deleted")
                data["Message"] = "Notebook Deleted"
                CFTSuccessResponse(event, "SUCCESS", data)
            except Exception as e:
                print(e)
                CFTFailedResponse(event, "FAILED", str(e))
            
        else:
            print("Notebook Invalid status")
            data["Message"] = "Notebook is not available"
            CFTSuccessResponse(event, "SUCCESS", data)
    
    if event['RequestType'] == 'Update':
        print("Update operation for Sagemaker Notebook is not recommended")
        data["Message"] = "Update operation for Sagemaker Notebook is not recommended"
        CFTSuccessResponse(event, "SUCCESS", data)
        
    
        
		    
